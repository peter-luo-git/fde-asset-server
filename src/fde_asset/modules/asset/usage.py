"""使用事件落库与复用通知判定。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Engine

from fde_asset.modules.asset.grading import refresh_grades
from fde_asset.core.db import asset_references, asset_usages, assets, record_event

EVENTS = {"loaded", "read", "applied"}


@dataclass
class UsageInput:
    ref: str = ""  # [[kind/name]] 或 kind/name
    asset_id: str = ""
    event: str = "loaded"
    session_id: str = ""
    engagement_slug: str = ""
    work_item_id: str = ""
    snapshot_sha: str = ""
    delivered_pr: bool = False
    actor_type: str = "agent"
    actor_id: str = ""


def _resolve_asset_id(conn, item: UsageInput) -> str | None:
    if item.asset_id:
        return item.asset_id
    from fde_asset.modules.asset.manifest import KIND_BY_SHORT

    ref = item.ref.strip().strip("[]")
    short_kind, _, name = ref.partition("/")
    kind = KIND_BY_SHORT.get(short_kind)
    if not kind or not name:
        return None
    # 匹配优先级：项目级 → 部门级 → 公司级
    rows = conn.execute(
        select(assets).where(
            assets.c.kind == kind, assets.c.name == name, assets.c.deleted_at.is_(None)
        )
    ).fetchall()
    if not rows:
        return None

    def rank(row: Any) -> int:
        if row.scope == "engagement" and row.engagement_slug == item.engagement_slug:
            return 0
        if row.scope == "department":
            return 1
        if row.scope == "company":
            return 2
        return 3

    return sorted(rows, key=rank)[0].asset_id


def record_usages(engine: Engine, items: list[UsageInput]) -> dict[str, Any]:
    accepted, unknown, duplicated, notifications = 0, [], 0, []
    with engine.begin() as conn:
        for item in items:
            if item.event not in EVENTS:
                unknown.append({"ref": item.ref, "reason": "event_unsupported"})
                continue
            asset_id = _resolve_asset_id(conn, item)
            if not asset_id:
                unknown.append({"ref": item.ref, "reason": "asset_not_found"})
                continue
            exists = conn.execute(
                select(asset_usages.c.id).where(
                    and_(
                        asset_usages.c.asset_id == asset_id,
                        asset_usages.c.session_id == item.session_id,
                        asset_usages.c.event == item.event,
                    )
                )
            ).first()
            if exists:
                duplicated += 1
                continue
            first_in_engagement = (
                conn.execute(
                    select(func.count())
                    .select_from(asset_usages)
                    .where(
                        and_(
                            asset_usages.c.asset_id == asset_id,
                            asset_usages.c.engagement_slug == item.engagement_slug,
                        )
                    )
                ).scalar_one()
                == 0
            )
            conn.execute(
                asset_usages.insert().values(
                    asset_id=asset_id,
                    event=item.event,
                    session_id=item.session_id,
                    engagement_slug=item.engagement_slug,
                    work_item_id=item.work_item_id,
                    snapshot_sha=item.snapshot_sha,
                    delivered_pr=item.delivered_pr,
                    actor_type=item.actor_type,
                    actor_id=item.actor_id,
                )
            )
            accepted += 1
            record_event(
                conn,
                f"asset.{item.event}",
                {
                    "asset_id": asset_id,
                    "engagement_slug": item.engagement_slug,
                    "session_id": item.session_id,
                    "event": item.event,
                },
            )
            row = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
            source = json.loads(row.source_json or "{}") if row else {}
            source_slug = source.get("engagementSlug") or (row.engagement_slug if row else "")
            if (
                first_in_engagement
                and item.delivered_pr
                and item.engagement_slug
                and item.engagement_slug != source_slug
            ):
                payload = {
                    "asset_id": asset_id,
                    "title": row.title if row else "",
                    "owner_ref": row.owner_ref if row else "",
                    "engagement_slug": item.engagement_slug,
                    "work_item_id": item.work_item_id,
                }
                record_event(conn, "asset.reused_first_time", payload)
                notifications.append(payload)
    if accepted:
        # 复用项目数可能变了，等级跟着重算
        refresh_grades(engine)
    return {
        "accepted": accepted,
        "duplicated": duplicated,
        "unknown": unknown,
        "notifications": notifications,
    }


def record_reference(
    engine: Engine,
    *,
    asset_id: str,
    source_type: str,
    source_id: str,
    engagement_slug: str = "",
    created_by: str = "",
    asset_version: str = "",
) -> bool:
    with engine.begin() as conn:
        exists = conn.execute(
            select(asset_references.c.id).where(
                and_(
                    asset_references.c.asset_id == asset_id,
                    asset_references.c.source_type == source_type,
                    asset_references.c.source_id == source_id,
                )
            )
        ).first()
        if exists:
            return False
        conn.execute(
            asset_references.insert().values(
                asset_id=asset_id,
                asset_version=asset_version,
                source_type=source_type,
                source_id=source_id,
                engagement_slug=engagement_slug,
                created_by=created_by,
            )
        )
        record_event(
            conn,
            "asset.referenced",
            {
                "asset_id": asset_id,
                "source_type": source_type,
                "source_id": source_id,
                "engagement_slug": engagement_slug,
            },
        )
    refresh_grades(engine)
    return True
