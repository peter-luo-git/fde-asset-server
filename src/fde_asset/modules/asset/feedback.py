"""反馈闭环：用过之后好不好用。

复用次数只能证明被打开过，证明不了有用。这里收三种最轻的表态：
helpful / not_helpful / outdated；选「已过时」时自动起草一份修订草稿交给负责人，
因为那一刻是最省力的沉淀时机——人正好知道哪里过时了。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from fde_asset.core.db import (
    asset_feedback,
    asset_recommendations,
    assets,
    record_event,
)
from fde_asset.modules.asset.visibility import visibility_clause
from fde_asset.platform.identity import Principal

VERDICTS = ("helpful", "not_helpful", "outdated")
VERDICT_LABEL = {
    "helpful": "有用",
    "not_helpful": "没帮上",
    "outdated": "已过时",
}


class FeedbackError(ValueError):
    pass


def submit(
    engine: Engine,
    principal: Principal,
    *,
    asset_id: str,
    verdict: str,
    note: str = "",
    engagement_slug: str = "",
    candidate_id: str = "",
) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise FeedbackError(f"不支持的反馈：{verdict}")
    if verdict in ("not_helpful", "outdated") and not note.strip():
        raise FeedbackError("说一句哪里不好，负责人才改得动")
    with engine.begin() as conn:
        visible = conn.execute(
            select(assets.c.asset_id).where(
                assets.c.asset_id == asset_id, visibility_clause(principal)
            )
        ).first()
        if visible is None:
            raise FeedbackError("资产不存在或无权访问")
        conn.execute(
            asset_feedback.insert().values(
                asset_id=asset_id,
                verdict=verdict,
                note=note,
                engagement_slug=engagement_slug,
                created_by=principal.user_id,
                candidate_id=candidate_id,
            )
        )
        record_event(
            conn,
            "asset.feedback",
            {
                "asset_id": asset_id,
                "verdict": verdict,
                "created_by": principal.user_id,
                "note": note,
            },
        )
    return {"asset_id": asset_id, "verdict": verdict, "candidate_id": candidate_id}


def attach_candidate(engine: Engine, asset_id: str, created_by: str, candidate_id: str) -> None:
    """把自动起草的修订草稿挂到最近一条「已过时」反馈上。"""
    with engine.begin() as conn:
        row = conn.execute(
            select(asset_feedback.c.id)
            .where(
                asset_feedback.c.asset_id == asset_id,
                asset_feedback.c.created_by == created_by,
                asset_feedback.c.verdict == "outdated",
                asset_feedback.c.candidate_id == "",
            )
            .order_by(asset_feedback.c.id.desc())
        ).first()
        if row is None:
            return
        conn.execute(
            asset_feedback.update()
            .where(asset_feedback.c.id == row.id)
            .values(candidate_id=candidate_id)
        )


def summary(engine: Engine, asset_id: str) -> dict[str, Any]:
    """单个资产的反馈汇总，详情页用。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(asset_feedback.c.verdict, func.count())
            .where(asset_feedback.c.asset_id == asset_id)
            .group_by(asset_feedback.c.verdict)
        ).fetchall()
        recent = conn.execute(
            select(asset_feedback)
            .where(asset_feedback.c.asset_id == asset_id)
            .order_by(asset_feedback.c.id.desc())
            .limit(10)
        ).fetchall()
    counts = {verdict: 0 for verdict in VERDICTS}
    for verdict, count in rows:
        counts[verdict] = count
    return {
        "counts": counts,
        "recent": [
            {
                "verdict": row.verdict,
                "label": VERDICT_LABEL[row.verdict],
                "note": row.note,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat(),
                "candidate_id": row.candidate_id,
            }
            for row in recent
        ],
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def owner_signals(engine: Engine, principal: Principal) -> dict[str, Any]:
    """负责人待办：我负责的资产收到的负面反馈，和被反复拒绝的推荐。

    拒绝原因是最精准的改进线索——说明摘要或适用边界写得不对。
    """
    owner_refs = [f"user:{principal.user_id}"]
    if principal.department_code:
        owner_refs.append(f"department:{principal.department_code}")

    with engine.connect() as conn:
        mine = {
            row.asset_id: row
            for row in conn.execute(select(assets).where(assets.c.owner_ref.in_(owner_refs)))
        }
        if not mine:
            return {"negative_feedback": [], "declined": []}
        feedback_rows = conn.execute(
            select(asset_feedback)
            .where(
                asset_feedback.c.asset_id.in_(list(mine)),
                asset_feedback.c.verdict.in_(["not_helpful", "outdated"]),
            )
            .order_by(asset_feedback.c.id.desc())
            .limit(50)
        ).fetchall()
        declined_rows = conn.execute(
            select(
                asset_recommendations.c.asset_id,
                func.count().label("times"),
            )
            .where(
                asset_recommendations.c.asset_id.in_(list(mine)),
                asset_recommendations.c.status == "declined",
            )
            .group_by(asset_recommendations.c.asset_id)
        ).fetchall()
        declined_notes = conn.execute(
            select(asset_recommendations.c.asset_id, asset_recommendations.c.note).where(
                asset_recommendations.c.asset_id.in_(list(mine)),
                asset_recommendations.c.status == "declined",
            )
        ).fetchall()

    notes_by_asset: dict[str, list[str]] = {}
    for row in declined_notes:
        if row.note:
            notes_by_asset.setdefault(row.asset_id, []).append(row.note)

    return {
        "negative_feedback": [
            {
                "asset_id": row.asset_id,
                "title": mine[row.asset_id].title,
                "verdict": row.verdict,
                "label": VERDICT_LABEL[row.verdict],
                "note": row.note,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat(),
                "candidate_id": row.candidate_id,
            }
            for row in feedback_rows
        ],
        "declined": [
            {
                "asset_id": row.asset_id,
                "title": mine[row.asset_id].title,
                "times": row.times,
                "notes": notes_by_asset.get(row.asset_id, []),
            }
            for row in sorted(declined_rows, key=lambda r: r.times, reverse=True)
        ],
    }


def revision_draft_payload(asset_row: Any, note: str, reporter: str) -> dict[str, Any]:
    """给「已过时」自动起草用的草稿内容：带上反馈人的原话。"""
    return {
        "kind": asset_row.kind,
        "name": f"{asset_row.name}-revision",
        "title": f"{asset_row.title}（修订）",
        "scope": asset_row.scope,
        "department_code": asset_row.department_code,
        "engagement_slug": asset_row.engagement_slug,
        "note": note,
        "reporter": reporter,
        "source_asset_id": asset_row.asset_id,
        "body": json.dumps({"反馈人": reporter, "过时原因": note}, ensure_ascii=False, indent=2),
    }
