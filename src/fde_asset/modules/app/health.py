"""应用资产的演示地址探活。

一条产品上的讲究：**探不到不等于挂了**。平台装在内网，公网应用它可能够不着；
本机应用它一定够不着。所以状态分四种，页面上分别说话：

| 状态 | 含义 | 页面怎么说 |
|---|---|---|
| online | 探通了 | 在线，可以点开 |
| offline | 探得到网络但服务没起来 | 演示环境离线，最近一次在线是 X |
| unreachable | 平台到不了那个网络 | 平台够不着（内网/VPN 应用），自己试试 |
| skipped | 登记为本机应用，不该探 | 本机应用，联系作者演示 |

**平台够不着的网络，借用户的电脑探**：平台部署在内网、不能出网时，公网应用它一个都探不到；
需要 VPN 的它也到不了。这时让打开页面的人的浏览器去连一下那个地址，把「连不连得上」报回来
（`report_from_browser`）。浏览器只能知道连没连上，看不到状态码，所以它报的在线不等于服务一切正常，
页面上会注明是谁的电脑、什么时候探的。平台自己探失败时，不会盖掉一天之内用户电脑报的「在线」。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from fde_asset.core.db import app_health, assets

#: 本机应用不探；平台跟它不在一个网络里
SKIP_NETWORKS = {"local"}
TIMEOUT_SECONDS = 5
#: 用户电脑报的「在线」多久之内算数，平台自己探不到时不去盖它
BROWSER_RESULT_TTL = timedelta(hours=24)


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
                select(app_health).where(app_health.c.asset_id == result.asset_id)
            ).first()
            if (
                result.status in ("offline", "unreachable")
                and previous is not None
                and previous.checked_via == "browser"
                and previous.status == "online"
                and moment - _aware(previous.checked_at) < BROWSER_RESULT_TTL
            ):
                # 平台探不到，但不久前有人用自己的电脑连上过：信那一次，别把它标成离线
                continue
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
                "checked_via": "server",
                "checked_by": "",
            }
            statement = sqlite_insert(app_health).values(**values)
            conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[app_health.c.asset_id],
                    set_={k: v for k, v in values.items() if k != "asset_id"},
                )
            )
    return results


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def report_from_browser(
    engine: Engine, rows: list[Any], reports: dict[str, dict[str, Any]], *, reporter: str
) -> list[ProbeResult]:
    """记下用户电脑探到的结果。`rows` 是这个人看得见的应用，`reports` 是 {asset_id: {reachable, latency_ms}}。

    连上了就是在线。没连上**不下「离线」的结论**，只记「够不着」：浏览器探活很容易被它自己的
    安全设置拦掉（强制 https、插件、代理），连不上不等于服务挂了。平台自己探出来的结果
    （在线或离线）更可靠，不会被某个人的「没连上」改掉。
    """
    moment = _now()
    results: list[ProbeResult] = []
    with engine.begin() as conn:
        for row in rows:
            report = reports.get(row.asset_id)
            if report is None:
                continue
            demo = _demo_of(row)
            url = str(demo.get("url") or "")
            network = str(demo.get("network") or "")
            if not url or network in SKIP_NETWORKS:
                continue
            previous = conn.execute(
                select(app_health).where(app_health.c.asset_id == row.asset_id)
            ).first()
            reachable = bool(report.get("reachable"))
            if (
                not reachable
                and previous is not None
                and previous.checked_via == "server"
                and previous.status in ("online", "offline")
            ):
                continue
            if (
                not reachable
                and previous is not None
                and previous.status == "online"
                and moment - _aware(previous.checked_at) < BROWSER_RESULT_TTL
            ):
                # 别的同事刚用自己的电脑连上过：一个人没连上，不推翻它
                continue
            try:
                latency = max(0, min(int(report.get("latency_ms") or 0), 600_000))
            except (TypeError, ValueError):
                latency = 0
            status = "online" if reachable else "unreachable"
            result = ProbeResult(
                row.asset_id,
                status,
                network,
                url,
                latency_ms=latency if reachable else 0,
                detail="从用户的电脑连上了"
                if reachable
                else "用户的电脑没连上（也可能是浏览器拦了）",
            )
            values = {
                "asset_id": row.asset_id,
                "status": status,
                "network": network,
                "url": url,
                "http_status": 0,
                "latency_ms": result.latency_ms,
                "detail": result.detail,
                "checked_at": moment,
                "last_online_at": moment
                if reachable
                else (previous.last_online_at if previous else None),
                "checked_via": "browser",
                "checked_by": reporter,
            }
            statement = sqlite_insert(app_health).values(**values)
            conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[app_health.c.asset_id],
                    set_={k: v for k, v in values.items() if k != "asset_id"},
                )
            )
            results.append(result)
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
            "checked_via": row.checked_via or "server",
            "checked_by": row.checked_by or "",
        }
        for row in rows
    }
