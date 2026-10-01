"""应用资产的演示地址探活。

一条产品上的讲究：**探不到不等于挂了**。平台装在内网，公网应用它可能够不着；
本机应用它一定够不着。所以状态分四种，页面上分别说话：

| 状态 | 含义 | 页面怎么说 |
|---|---|---|
| online | 探通了 | 在线，可以点开 |
| offline | 探得到网络但服务没起来 | 演示环境离线，最近一次在线是 X |
| unreachable | 平台到不了那个网络 | 平台够不着（内网/VPN 应用），自己试试 |
| skipped | 登记为本机应用，不该探 | 本机应用，联系作者演示 |
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from fde_asset.core.db import app_health, assets

#: 本机应用不探；平台跟它不在一个网络里
SKIP_NETWORKS = {"local"}
TIMEOUT_SECONDS = 5


@dataclass
class ProbeResult:
    asset_id: str
    status: str
    network: str
    url: str
    http_status: int = 0
    latency_ms: int = 0
    detail: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _demo_of(row: Any) -> dict[str, Any]:
    spec = json.loads(row.kind_spec_json or "{}")
    demo = spec.get("demo") or {}
    return demo if isinstance(demo, dict) else {}


def probe_one(row: Any, fetch: Callable[[str], tuple[int, str]]) -> ProbeResult:
    demo = _demo_of(row)
    url = str(demo.get("url") or "")
    network = str(demo.get("network") or "")
    if not url:
        return ProbeResult(row.asset_id, "skipped", network, "", detail="没有登记演示地址")
    if network in SKIP_NETWORKS:
        return ProbeResult(row.asset_id, "skipped", network, url, detail="本机应用，平台不探")

    started = time.monotonic()
    try:
        status_code, detail = fetch(url)
    except Exception as exc:  # noqa: BLE001 - 探活失败的原因五花八门，都当作够不着
        # 公网应用探不通基本就是真挂了；内网或 VPN 的，更可能是平台够不着
        status = "offline" if network == "internet" else "unreachable"
        return ProbeResult(row.asset_id, status, network, url, detail=str(exc)[:200])
    latency = int((time.monotonic() - started) * 1000)
    ok = 200 <= status_code < 400
    return ProbeResult(
        row.asset_id,
        "online" if ok else "offline",
        network,
        url,
        http_status=status_code,
        latency_ms=latency,
        detail=detail[:200],
    )


def _default_fetch(url: str) -> tuple[int, str]:  # pragma: no cover - 需要真实网络
    import httpx

    response = httpx.get(url, timeout=TIMEOUT_SECONDS, follow_redirects=True)
    return response.status_code, ""


def probe_all(
    engine: Engine, *, fetch: Callable[[str], tuple[int, str]] | None = None
) -> list[ProbeResult]:
    """探一遍所有应用资产，把结果写进 app_health。"""
    fetch = fetch or _default_fetch
    with engine.connect() as conn:
        rows = conn.execute(
            select(assets).where(
                assets.c.kind == "Application",
                assets.c.deleted_at.is_(None),
                assets.c.valid.is_(True),
            )
        ).fetchall()

    results = [probe_one(row, fetch) for row in rows]
    moment = _now()
    with engine.begin() as conn:
        for result in results:
            previous = conn.execute(
                select(app_health.c.last_online_at).where(app_health.c.asset_id == result.asset_id)
            ).first()
            last_online = (
                moment
                if result.status == "online"
                else (previous.last_online_at if previous else None)
            )
            values = {
                "asset_id": result.asset_id,
                "status": result.status,
                "network": result.network,
                "url": result.url,
                "http_status": result.http_status,
                "latency_ms": result.latency_ms,
                "detail": result.detail,
                "checked_at": moment,
                "last_online_at": last_online,
            }
            statement = sqlite_insert(app_health).values(**values)
            conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[app_health.c.asset_id],
                    set_={k: v for k, v in values.items() if k != "asset_id"},
                )
            )
    return results


def health_map(engine: Engine) -> dict[str, dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(select(app_health)).fetchall()
    return {
        row.asset_id: {
            "status": row.status,
            "network": row.network,
            "url": row.url,
            "http_status": row.http_status,
            "latency_ms": row.latency_ms,
            "detail": row.detail,
            "checked_at": row.checked_at.isoformat() if row.checked_at else "",
            "last_online_at": row.last_online_at.isoformat() if row.last_online_at else "",
        }
        for row in rows
    }
