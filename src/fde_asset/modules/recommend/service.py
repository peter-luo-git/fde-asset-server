"""推荐：为项目和 Agent 找资产，并把"推给谁、收不收"这件事记下来。

与匹配的分工：
- `modules/asset/matching` 回答"相关不相关"（内容理解，见设计文档 §1.4b）；
- 这里回答"给谁、谁决定、收了之后关联到哪"（寻址与状态）。

标识（engagement_slug / agent_key）只是收件地址，不参与相关性判断。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.engine import Engine

from fde_asset.core.db import (
    asset_recommendations,
    assets,
    record_event,
    target_assets,
)
from fde_asset.modules.asset import matching, rerank
from fde_asset.modules.asset.visibility import visibility_clause
from fde_asset.platform.identity import Principal

TARGET_TYPES = ("engagement", "agent")
#: 一次推荐最多给多少条，再多人就不看了
DEFAULT_LIMIT = 10


class RecommendError(ValueError):
    pass


@dataclass
class Target:
    """推荐的收件地址 + 用来做内容匹配的上下文。"""

    target_type: str
    target_id: str
    title: str = ""
    description: str = ""
    industry: str = ""
    department_code: str = ""
    owner: str = ""
    stage: str = ""
    role: str = ""

    def context(self) -> matching.MatchContext:
        return matching.MatchContext(
            title=self.title or self.target_id,
            description=self.description,
            industry=self.industry,
            department_code=self.department_code,
            engagement_slug=self.target_id if self.target_type == "engagement" else "",
            agent_role=self.role,
            stage=self.stage,
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def target_from_directory(directory, target_type: str, target_id: str) -> Target:
    """按目录里的登记信息组装目标；查不到就只留标识，匹配退化成按名字猜。"""
    if target_type not in TARGET_TYPES:
        raise RecommendError(f"不支持的目标类型：{target_type}")
    source = directory.engagements() if target_type == "engagement" else directory.agents()
    raw = source.get(target_id, {})
    return Target(
        target_type=target_type,
        target_id=target_id,
        title=raw.get("title", ""),
        description=raw.get("description", ""),
        industry=raw.get("industry", ""),
        department_code=raw.get("department_code", ""),
        owner=raw.get("owner", ""),
        stage=raw.get("stage", ""),
        role=raw.get("role", ""),
    )


def department_targets(directory, department_code: str) -> list[Target]:
    """部门管理员视角：本部门的全部项目与 Agent。"""
    found: list[Target] = []
    for target_type, source in (
        ("engagement", directory.engagements()),
        ("agent", directory.agents()),
    ):
        for target_id, raw in source.items():
            if raw.get("department_code") == department_code:
                found.append(target_from_directory(directory, target_type, target_id))
    return found


def compute(
    engine: Engine,
    principal: Principal,
    target: Target,
    *,
    limit: int = DEFAULT_LIMIT,
    llm: Any = None,
    reranker: Any = None,
) -> tuple[list[dict[str, Any]], str]:
    """算出候选推荐（不落库），返回（结果, 用的什么模式）。

    两步：结构化粗排把全库收敛到模型读得完的集合，再交给模型按内容理解精排并写理由。
    没配模型或模型出错就只用粗排，推荐不会因此瘫掉。
    """
    result = matching.match(engine, principal, target.context())
    flat: list[dict[str, Any]] = []
    for group in ("rules", "sops", "skills", "knowledge"):
        for item in result[group]:
            flat.append({**item, "group": group})
    flat.sort(key=lambda item: item["score"], reverse=True)

    linked = {row.asset_id for row in _linked_assets(engine, target.target_type, target.target_id)}
    # 已经关联过的不再推荐，免得同一份资产反复出现在待办里
    pool = [item for item in flat if item["asset_id"] not in linked]

    # 规范是红线，不参与精排，永远带上
    rules = [item for item in pool if item["group"] == "rules"]
    others = [item for item in pool if item["group"] != "rules"]
    picked, mode = rerank.rerank(
        others,
        {
            "标题": target.title or target.target_id,
            "描述": target.description,
            "行业": target.industry,
            "阶段": target.stage,
            "类型": "项目" if target.target_type == "engagement" else "Agent",
            "Agent 角色": target.role,
        },
        llm,
        limit=max(limit - len(rules), 1),
        reranker=reranker,
    )
    return (rules + picked)[:limit], mode


def _linked_assets(engine: Engine, target_type: str, target_id: str):
    with engine.connect() as conn:
        return conn.execute(
            select(target_assets.c.asset_id).where(
                and_(
                    target_assets.c.target_type == target_type,
                    target_assets.c.target_id == target_id,
                )
            )
        ).fetchall()


def visible_to(engine: Engine, principal: Principal, asset_ids: list[str]) -> set[str]:
    """这批资产里，这个人能看见哪些。"""
    if not asset_ids:
        return set()
    with engine.connect() as conn:
        rows = conn.execute(
            select(assets.c.asset_id).where(
                assets.c.asset_id.in_(asset_ids), visibility_clause(principal)
            )
        ).fetchall()
    return {row.asset_id for row in rows}


def save(
    engine: Engine,
    principal: Principal,
    target: Target,
    items: list[dict[str, Any]],
    *,
    source: str = "self",
    status: str = "suggested",
) -> dict[str, Any]:
    """落库。同一个（资产，目标）只保留一条活跃推荐，重复推送只更新不新增。"""
    created = updated = skipped = 0
    with engine.begin() as conn:
        for item in items:
            existing = conn.execute(
                select(asset_recommendations).where(
                    and_(
                        asset_recommendations.c.asset_id == item["asset_id"],
                        asset_recommendations.c.target_type == target.target_type,
                        asset_recommendations.c.target_id == target.target_id,
                    )
                )
            ).first()
            reasons = json.dumps(item.get("reasons", []), ensure_ascii=False)
            if existing is None:
                conn.execute(
                    asset_recommendations.insert().values(
                        recommendation_id=uuid.uuid4().hex[:16],
                        asset_id=item["asset_id"],
                        target_type=target.target_type,
                        target_id=target.target_id,
                        target_owner=target.owner,
                        source=source,
                        recommended_by=principal.user_id,
                        reason_json=reasons,
                        score=float(item.get("score", 0)),
                        status=status,
                    )
                )
                created += 1
            elif existing.status in ("accepted", "declined"):
                # 已经表过态的不再打扰，除非对方自己改主意
                skipped += 1
            else:
                conn.execute(
                    update(asset_recommendations)
                    .where(asset_recommendations.c.recommendation_id == existing.recommendation_id)
                    .values(
                        reason_json=reasons,
                        score=float(item.get("score", 0)),
                        status=status,
                        recommended_by=principal.user_id,
                        source=source,
                        updated_at=_now(),
                    )
                )
                updated += 1
        if status == "sent" and (created or updated):
            record_event(
                conn,
                "asset.recommended",
                {
                    "target_type": target.target_type,
                    "target_id": target.target_id,
                    "target_owner": target.owner,
                    "recommended_by": principal.user_id,
                    "count": created + updated,
                },
            )
    return {"created": created, "updated": updated, "skipped": skipped}


def _rows_to_items(engine: Engine, principal: Principal, rows) -> list[dict[str, Any]]:
    """补上资产标题等展示字段，并且只返回当前身份看得见的资产。"""
    ids = [row.asset_id for row in rows]
    if not ids:
        return []
    with engine.connect() as conn:
        visible = {
            r.asset_id: r
            for r in conn.execute(
                select(assets).where(assets.c.asset_id.in_(ids), visibility_clause(principal))
            )
        }
    items = []
    for row in rows:
        asset = visible.get(row.asset_id)
        if asset is None:
            continue
        items.append(
            {
                "recommendation_id": row.recommendation_id,
                "asset_id": row.asset_id,
                "kind": asset.kind,
                "title": asset.title,
                "summary": asset.summary,
                "ref": f"[[{asset.kind.lower()}/{asset.name}]]",
                "quality": asset.quality,
                "scope": asset.scope,
                "owner_ref": asset.owner_ref,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "target_owner": row.target_owner,
                "source": row.source,
                "recommended_by": row.recommended_by,
                "reasons": json.loads(row.reason_json or "[]"),
                "score": row.score,
                "status": row.status,
                "decided_by": row.decided_by,
                "decided_at": row.decided_at.isoformat() if row.decided_at else "",
                "note": row.note,
                "created_at": row.created_at.isoformat(),
            }
        )
    return items


def list_for_target(
    engine: Engine,
    principal: Principal,
    target_type: str,
    target_id: str,
    *,
    status: str = "",
) -> list[dict[str, Any]]:
    statement = select(asset_recommendations).where(
        and_(
            asset_recommendations.c.target_type == target_type,
            asset_recommendations.c.target_id == target_id,
        )
    )
    if status:
        statement = statement.where(asset_recommendations.c.status == status)
    with engine.connect() as conn:
        rows = conn.execute(statement.order_by(asset_recommendations.c.score.desc())).fetchall()
    return _rows_to_items(engine, principal, rows)


def inbox(engine: Engine, principal: Principal) -> list[dict[str, Any]]:
    """待我确认：别人推给我负责的项目或 Agent，还没表态的。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(asset_recommendations)
            .where(
                and_(
                    asset_recommendations.c.target_owner == principal.user_id,
                    asset_recommendations.c.status == "sent",
                )
            )
            .order_by(asset_recommendations.c.score.desc())
        ).fetchall()
    return _rows_to_items(engine, principal, rows)


def decide(
    engine: Engine,
    principal: Principal,
    recommendation_id: str,
    *,
    accept: bool,
    note: str = "",
) -> dict[str, Any]:
    """接收就写关联；忽略要留原因——反复被拒是资产该改的信号。"""
    with engine.begin() as conn:
        row = conn.execute(
            select(asset_recommendations).where(
                asset_recommendations.c.recommendation_id == recommendation_id
            )
        ).first()
        if row is None:
            raise RecommendError("推荐不存在")
        if row.status in ("accepted", "declined"):
            return {
                "recommendation_id": recommendation_id,
                "status": row.status,
                "idempotent": True,
            }
        if not accept and not note.strip():
            raise RecommendError("忽略要写原因，这是资产改进最直接的线索")

        status = "accepted" if accept else "declined"
        conn.execute(
            update(asset_recommendations)
            .where(asset_recommendations.c.recommendation_id == recommendation_id)
            .values(
                status=status,
                decided_by=principal.user_id,
                decided_at=_now(),
                note=note,
                updated_at=_now(),
            )
        )
        if accept:
            exists = conn.execute(
                select(target_assets.c.id).where(
                    and_(
                        target_assets.c.target_type == row.target_type,
                        target_assets.c.target_id == row.target_id,
                        target_assets.c.asset_id == row.asset_id,
                    )
                )
            ).first()
            if exists is None:
                conn.execute(
                    target_assets.insert().values(
                        target_type=row.target_type,
                        target_id=row.target_id,
                        asset_id=row.asset_id,
                        source="recommendation",
                        created_by=principal.user_id,
                    )
                )
        record_event(
            conn,
            f"recommendation.{status}",
            {
                "recommendation_id": recommendation_id,
                "asset_id": row.asset_id,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "decided_by": principal.user_id,
                "note": note,
            },
        )
    return {"recommendation_id": recommendation_id, "status": status}


def link(
    engine: Engine,
    principal: Principal,
    target_type: str,
    target_id: str,
    asset_id: str,
) -> dict[str, Any]:
    """本人直接关联，不走推荐流程。"""
    with engine.begin() as conn:
        exists = conn.execute(
            select(target_assets.c.id).where(
                and_(
                    target_assets.c.target_type == target_type,
                    target_assets.c.target_id == target_id,
                    target_assets.c.asset_id == asset_id,
                )
            )
        ).first()
        if exists is not None:
            return {"linked": False, "reason": "已经关联过"}
        conn.execute(
            target_assets.insert().values(
                target_type=target_type,
                target_id=target_id,
                asset_id=asset_id,
                source="manual",
                created_by=principal.user_id,
            )
        )
        # 同一份资产如果还有待确认的推荐，一并置为已接受，避免收件箱里留着废条目
        conn.execute(
            update(asset_recommendations)
            .where(
                and_(
                    asset_recommendations.c.asset_id == asset_id,
                    asset_recommendations.c.target_type == target_type,
                    asset_recommendations.c.target_id == target_id,
                    asset_recommendations.c.status.in_(["suggested", "sent"]),
                )
            )
            .values(status="accepted", decided_by=principal.user_id, decided_at=_now())
        )
        record_event(
            conn,
            "asset.linked",
            {
                "asset_id": asset_id,
                "target_type": target_type,
                "target_id": target_id,
                "created_by": principal.user_id,
            },
        )
    return {"linked": True}


def linked_assets(
    engine: Engine, principal: Principal, target_type: str, target_id: str
) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(target_assets, assets)
            .join(assets, assets.c.asset_id == target_assets.c.asset_id)
            .where(
                and_(
                    target_assets.c.target_type == target_type,
                    target_assets.c.target_id == target_id,
                    visibility_clause(principal),
                )
            )
        ).fetchall()
    return [
        {
            "asset_id": row.asset_id,
            "kind": row.kind,
            "title": row.title,
            "summary": row.summary,
            "ref": f"[[{row.kind.lower()}/{row.name}]]",
            "source": row.source,
            "created_by": row.created_by,
        }
        for row in rows
    ]
