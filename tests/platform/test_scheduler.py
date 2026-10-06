"""后台定时任务：仓库有新提交才重建索引、定时扫线索、定时探活；一个坏了不拖累其它。"""

from __future__ import annotations

import threading
import time

from sqlalchemy import select

from fde_asset.core.db import asset_events, asset_leads, assets
from fde_asset.modules.app import health as app_health
from fde_asset.platform import settings_store
from fde_asset.scheduler import Task, build_tasks, run_due, run_forever

CASE_YAML = "knowledge/cases/oracle-to-pg-sequence-gap/asset.yaml"


def _task(context, name: str) -> Task:
    return next(task for task in build_tasks(context) if task.name == name)


def _company(context):
    return next(repo for repo in context.repos() if repo.name == "company-assets")


def _index_events(context) -> int:
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(asset_events.c.type).where(asset_events.c.type == "asset.indexed")
        )
        return len(rows.fetchall())


def test_reindex_only_runs_when_a_repo_has_new_commits(indexed) -> None:
    task = _task(indexed, "reindex")
    events = _index_events(indexed)

    assert task.run() == {"changed": []}
    assert task.run() == {"changed": []}
    assert _index_events(indexed) == events, "没有新提交就不该重建，也不该刷索引事件"

    # 有人绕过平台直接改了仓库里的文件（这正是 v0.1 的编辑路径）
    repo = _company(indexed)
    text = indexed.repo_port.read_file(repo, CASE_YAML).decode("utf-8")
    assert "序列缓存导致跳号" in text
    changed = text.replace("序列缓存导致跳号", "定时任务看见了这次改动：序列缓存导致跳号")
    indexed.repo_port.commit_files(repo, "main", {CASE_YAML: changed.encode("utf-8")}, "改摘要")

    result = task.run()
    assert result["changed"] == ["company-assets"]
    with indexed.engine.connect() as conn:
        summary = conn.execute(
            select(assets.c.summary).where(assets.c.name == "oracle-to-pg-sequence-gap")
        ).scalar_one()
    assert summary.startswith("定时任务看见了这次改动")
    assert task.run() == {"changed": []}, "同一个提交只处理一次"


def test_fresh_worker_does_not_reindex_what_is_already_indexed(indexed) -> None:
    """接口进程启动时已经建过索引，独立的 worker 进程起来不该再来一遍。"""
    events = _index_events(indexed)
    assert _task(indexed, "reindex").run() == {"changed": []}
    assert _index_events(indexed) == events


def test_leads_task_scans_activity(indexed) -> None:
    with indexed.engine.connect() as conn:
        assert conn.execute(select(asset_leads)).fetchall() == []
    result = _task(indexed, "leads").run()
    assert result["created"] > 0
    assert _task(indexed, "leads").run()["created"] == 0, "重复扫描不重复建"


def test_probe_task_uses_the_configured_interval(indexed, monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(app_health, "probe_all", lambda engine: calls.append(1) or [1, 2])
    task = _task(indexed, "probe")

    assert task.run() == {"checked": 2}
    assert task.interval() == 600.0
    settings_store.update(indexed.engine, {"app_probe_interval_minutes": 3}, updated_by="admin")
    assert task.interval() == 180.0, "系统配置改了，下一轮就按新的间隔"


def test_run_due_respects_intervals_and_survives_failures() -> None:
    ran: list[str] = []

    def boom() -> dict:
        ran.append("bad")
        raise RuntimeError("仓库读不了")

    tasks = [
        Task("bad", lambda: 30.0, boom),
        Task("good", lambda: 60.0, lambda: ran.append("good") or {"ok": True}),
    ]

    first = run_due(tasks, now=100.0)
    assert first == {"bad": {"error": "仓库读不了"}, "good": {"ok": True}}, "一个坏了，另一个照跑"
    assert run_due(tasks, now=110.0) == {}, "没到点的不跑"
    assert set(run_due(tasks, now=131.0)) == {"bad"}, "出过错的任务到点照样重试"
    assert set(run_due(tasks, now=161.0)) == {"bad", "good"}
    assert ran == ["bad", "good", "bad", "bad", "good"]


def test_run_forever_stops_when_asked(indexed) -> None:
    stop = threading.Event()
    thread = threading.Thread(
        target=run_forever, args=(indexed,), kwargs={"tick": 0.05, "stop": stop}
    )
    thread.start()
    time.sleep(0.3)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
