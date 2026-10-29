"""可见范围申请：资产负责人想把资产放到自己没有评审权的那一层时，提交申请，由那一层的评审人审批。

以前这种情况直接 403，负责人只能线下找人（2026-10-17 改）。

- 一份申请只申请加一项（全公司、某部门、某客户、某项目）；一次调整里有几项要审批就生成几份；
- 谁能批：那一项的评审人，和直接调整时的规则一样（`sharing._may_add`）；
- 批准后按资产**当时**的可见范围加上这一项再提交，期间别人改过也不会被覆盖；
- 起草时多选的可见范围，评审人没权放的那几项，入库后也转成申请（申请人记为起草人）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets, record_event, visibility_requests
from fde_asset.modules.asset.visibility import can_review
from fde_asset.platform.identity import Principal

LABEL = {"company": "全公司", "department": "部门", "customer": "客户", "engagement": "项目"}


class RequestError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def split(entry: str) -> tuple[str, str]:
    audience, _, code = entry.partition(":")
    return audience, code


def describe(entry: str) -> str:
    audience, code = split(entry)
    return LABEL.get(audience, audience) + (f" {code}" if code else "")


def may_decide(principal: Principal, entry: str) -> bool:
    audience, code = split(entry)
    if audience not in LABEL:
        return False
    return can_review(
        principal,
        audience,
        department_code=code if audience == "department" else "",
        customer_code=code if audience == "customer" else "",
        engagement_slug=code if audience == "engagement" else "",
    )


def open_requests(engine: Engine, asset_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(visibility_requests)
            .where(
                visibility_requests.c.asset_id == asset_id,
                visibility_requests.c.status == "open",
            )
            .order_by(visibility_requests.c.created_at)
        ).fetchall()
    return [_view(row) for row in rows]


def create(
    engine: Engine, *, asset_id: str, entries: list[str], requested_by: str, note: str = ""
) -> list[dict[str, Any]]:
    """为每一项建一份申请；同一份资产同一项已经有在等的，就不再重复建。返回这几项现在的申请。"""
    created: list[dict[str, Any]] = []
    with engine.begin() as conn:
        for entry in dict.fromkeys(entries):
            existing = conn.execute(
                select(visibility_requests).where(
                    visibility_requests.c.asset_id == asset_id,
                    visibility_requests.c.entry == entry,
                    visibility_requests.c.status == "open",
                )
            ).first()
            if existing is not None:
                created.append(_view(existing))
                continue
            request_id = uuid.uuid4().hex[:12]
            conn.execute(
                visibility_requests.insert().values(
                    request_id=request_id,
                    asset_id=asset_id,
                    entry=entry,
                    requested_by=requested_by,
                    note=note,
                    status="open",
                )
            )
            record_event(
                conn,
                "asset.visibility_requested",
                {
                    "request_id": request_id,
                    "asset_id": asset_id,
                    "entry": entry,
                    "by": requested_by,
                },
            )
            created.append(
                {
                    "request_id": request_id,
                    "asset_id": asset_id,
                    "entry": entry,
                    "label": describe(entry),
                    "requested_by": requested_by,
                    "note": note,
                    "status": "open",
                    "decided_by": "",
                    "decision_note": "",
                    "created_at": _now().isoformat(),
                    "decided_at": "",
                }
            )
    return created


def get(engine: Engine, request_id: str) -> Any:
    with engine.connect() as conn:
        return conn.execute(
            select(visibility_requests).where(visibility_requests.c.request_id == request_id)
        ).first()


def listing(engine: Engine, principal: Principal, *, view: str = "todo") -> list[dict[str, Any]]:
    """`todo`：等我批的；`mine`：我提的；`decided`：我批过的。"""
    if view not in {"todo", "mine", "decided"}:
        raise RequestError("不支持的申请列表视图")
    query = select(visibility_requests, assets.c.title, assets.c.kind).select_from(
        visibility_requests.outerjoin(assets, assets.c.asset_id == visibility_requests.c.asset_id)
    )
    if view == "mine":
        query = query.where(visibility_requests.c.requested_by == principal.user_id)
    elif view == "decided":
        query = query.where(visibility_requests.c.decided_by == principal.user_id)
    else:
        query = query.where(visibility_requests.c.status == "open")
    with engine.connect() as conn:
        rows = conn.execute(query.order_by(visibility_requests.c.created_at.desc())).fetchall()
    result = []
    for row in rows:
        if view == "todo" and (
            not may_decide(principal, row.entry) or row.requested_by == principal.user_id
        ):
            continue
        item = _view(row)
        item["title"] = row.title or "（资产已不存在）"
        item["kind"] = row.kind or ""
        item["can_decide"] = row.status == "open" and may_decide(principal, row.entry)
        result.append(item)
    return result


def mark(
    engine: Engine,
    request_id: str,
    *,
    status: str,
    by: str,
    note: str = "",
) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(visibility_requests)
            .where(
                visibility_requests.c.request_id == request_id,
                visibility_requests.c.status == "open",
            )
            .values(status=status, decided_by=by, decision_note=note, decided_at=_now())
        )
        record_event(
            conn,
            f"asset.visibility_request_{status}",
            {"request_id": request_id, "by": by, "note": note},
        )


def _view(row: Any) -> dict[str, Any]:
    return {
        "request_id": row.request_id,
        "asset_id": row.asset_id,
        "entry": row.entry,
        "label": describe(row.entry),
        "requested_by": row.requested_by,
        "note": row.note,
        "status": row.status,
        "decided_by": row.decided_by,
        "decision_note": row.decision_note,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "decided_at": row.decided_at.isoformat() if row.decided_at else "",
    }
