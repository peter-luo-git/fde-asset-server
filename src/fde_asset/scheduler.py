"""后台定时任务：不靠人点按钮、不靠重启，资产中心自己该做的事。

| 任务 | 做什么 | 多久一次 |
|---|---|---|
| reindex | 资产仓库有新提交就重建索引（顺带重建关系、发变更通知） | `index_poll_seconds`，默认 30 秒 |
| leads | 重新扫描沉淀线索 | 10 分钟 |
| probe | 探一遍应用的演示地址 | 系统配置 `app_probe_interval_minutes`，默认 10 分钟 |

正式部署是独立的 asset-worker 进程（`python -m fde_asset.main_asset_worker`）；
本地演示为了少起一个进程，`serve_seeded.py --with-worker` 会在接口进程里起一个线程跑同一套任务。
任何一个任务出错都只记日志，不影响其它任务，也不让进程退出。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select

from fde_asset.api.deps import ServiceContext
from fde_asset.core.db import assets
from fde_asset.modules.app import health as app_health
from fde_asset.modules.asset.indexer import index_all
from fde_asset.modules.leads import rules as leads_rules
from fde_asset.platform import settings_store

logger = logging.getLogger(__name__)

LEADS_INTERVAL_SECONDS = 600


@dataclass
class Task:
    name: str
    #: 每次排下一轮时现取，系统配置改了不用重启
    interval: Callable[[], float]
    run: Callable[[], dict[str, Any]]
    next_at: float = 0.0


def _indexed_heads(context: ServiceContext) -> dict[str, set[str]]:
    with context.engine.connect() as conn:
        rows = conn.execute(select(assets.c.repo, assets.c.commit_sha).distinct()).fetchall()
    heads: dict[str, set[str]] = {}
    for row in rows:
        heads.setdefault(row.repo, set()).add(row.commit_sha)
    return heads


def build_tasks(context: ServiceContext) -> list[Task]:
    seen_heads: dict[str, str] = {}

    def reindex() -> dict[str, Any]:
        """只在仓库有新提交时才动：每 30 秒无差别重建一遍没有意义，还会刷出一堆索引事件。"""
        indexed = _indexed_heads(context)
        changed: list[str] = []
        for repo in context.repos():
            head = context.repo_port.get_head(repo)
            if seen_heads.get(repo.name) == head:
                continue
            if head in indexed.get(repo.name, set()):
                seen_heads[repo.name] = head  # 库里已经是这个版本，记一下就行
                continue
            changed.append(repo.name)
            seen_heads[repo.name] = head
        if not changed:
            return {"changed": []}
        reports = index_all(
            context.engine,
            context.repo_port,
            context.repos(),
            text_limit=context.settings.index_text_limit,
        )
        return {"changed": changed, "indexed": sum(report.indexed for report in reports)}

    def leads() -> dict[str, Any]:
        return leads_rules.refresh(context.engine, context.activity)

    def probe() -> dict[str, Any]:
        return {"checked": len(app_health.probe_all(context.engine))}

    def probe_interval() -> float:
        minutes = int(settings_store.get(context.engine, "app_probe_interval_minutes") or 10)
        return max(minutes, 1) * 60.0

    return [
        Task("reindex", lambda: float(context.settings.index_poll_seconds), reindex),
        Task("leads", lambda: float(LEADS_INTERVAL_SECONDS), leads),
        Task("probe", probe_interval, probe),
    ]


def run_due(tasks: list[Task], now: float) -> dict[str, Any]:
    """跑一遍到点的任务，返回各自的结果（出错的记成 `{"error": ...}`）。"""
    results: dict[str, Any] = {}
    for task in tasks:
        if now < task.next_at:
            continue
        try:
            results[task.name] = task.run()
        except Exception as exc:  # noqa: BLE001 - 一个任务坏了不能拖垮其它任务
            logger.exception("定时任务 %s 失败", task.name)
            results[task.name] = {"error": str(exc)}
        task.next_at = now + task.interval()
    return results


def run_forever(
    context: ServiceContext, *, tick: float = 5.0, stop: threading.Event | None = None
) -> None:
    tasks = build_tasks(context)
    logger.info("定时任务启动：%s", "、".join(task.name for task in tasks))
    stop = stop or threading.Event()
    while not stop.is_set():
        for name, result in run_due(tasks, time.monotonic()).items():
            if result and result != {"changed": []}:
                logger.info("定时任务 %s：%s", name, result)
        stop.wait(tick)


def start_in_thread(context: ServiceContext) -> threading.Event:
    """在当前进程里起一个守护线程跑定时任务，返回用来叫停它的开关。"""
    stop = threading.Event()
    thread = threading.Thread(
        target=run_forever, args=(context,), kwargs={"stop": stop}, name="asset-worker", daemon=True
    )
    thread.start()
    return stop
