"""资产关系：把散落的单点连成一条链。

"方案 → 实施 → 问题 → 经验" 是一条真实存在的知识链，但资产是一份份存进来的，
不连起来就永远只能看到单点。这里做三件事：

1. **解析引用**：索引时写下的 `kind/name` 补上真正的 asset_id（写的时候对方可能还没入库）
2. **自动建链**：同一个项目沉淀出来的资产互相挂上；应用挂上它所在项目的产出
3. **读图**：按当前身份的可见范围，给出某份资产的上下游

自动建链用 source="auto" 标记，每次重建先清空再写，所以可以反复跑。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, delete, insert, select
from sqlalchemy.engine import Engine

from fde_asset.core.db import asset_relations, assets
from fde_asset.modules.asset.visibility import visibility_clause
from fde_asset.platform.identity import Principal

#: 同项目资产互相挂链时，单个资产最多挂多少条，免得一个大项目连成一张糊掉的网
SAME_ENGAGEMENT_CAP = 12

RELATION_LABEL = {
    "relatedTo": "引用",
    "sameEngagement": "同项目沉淀",
    "produced": "项目产出",
    "implements": "实施了",
    "supersedes": "取代",
    "dependsOn": "依赖",
}


def resolve_targets(engine: Engine) -> int:
    """把 to_ref（kind/name）解析成 to_asset_id。返回补上的条数。"""
    with engine.begin() as conn:
        by_ref = {
            f"{row.kind.lower()}/{row.name}": row.asset_id
            for row in conn.execute(
                select(assets.c.asset_id, assets.c.kind, assets.c.name).where(
                    assets.c.deleted_at.is_(None)
                )
            )
        }
        rows = conn.execute(
            select(asset_relations).where(asset_relations.c.to_asset_id.is_(None))
        ).fetchall()
        filled = 0
        for row in rows:
            ref = (row.to_ref or "").strip().strip("[]")
            target = by_ref.get(ref.lower())
            if target is None:
                continue
            conn.execute(
                asset_relations.update()
                .where(asset_relations.c.id == row.id)
                .values(to_asset_id=target)
            )
            filled += 1
    return filled


def _engagement_of(row: Any) -> str:
    import json

    source = json.loads(row.source_json or "{}")
    return str(source.get("engagementSlug") or row.engagement_slug or "")


def auto_link(engine: Engine) -> dict[str, int]:
    """自动建链。先清掉上一轮的自动关系，再重建，所以可以反复跑。"""
    with engine.begin() as conn:
        conn.execute(delete(asset_relations).where(asset_relations.c.source == "auto"))
        rows = conn.execute(
            select(assets).where(assets.c.deleted_at.is_(None), assets.c.valid.is_(True))
        ).fetchall()

        buckets: dict[str, list[Any]] = {}
        for row in rows:
            slug = _engagement_of(row)
            if slug:
                buckets.setdefault(slug, []).append(row)

        created = 0
        app_links = 0
        for slug, group in buckets.items():
            if len(group) < 2:
                continue
            apps = [row for row in group if row.kind == "Application"]
            for row in group[:SAME_ENGAGEMENT_CAP]:
                for other in group[:SAME_ENGAGEMENT_CAP]:
                    if row.asset_id == other.asset_id:
                        continue
                    conn.execute(
                        insert(asset_relations).values(
                            from_asset_id=row.asset_id,
                            to_ref=f"{other.kind.lower()}/{other.name}",
                            to_asset_id=other.asset_id,
                            type="sameEngagement",
                            source="auto",
                        )
                    )
                    created += 1
            # 应用是这个项目的"壳"，它产出了什么单独标出来
            for app in apps:
                for other in group:
                    if other.asset_id == app.asset_id or other.kind == "Application":
                        continue
                    conn.execute(
                        insert(asset_relations).values(
                            from_asset_id=app.asset_id,
                            to_ref=f"{other.kind.lower()}/{other.name}",
                            to_asset_id=other.asset_id,
                            type="produced",
                            source="auto",
                        )
                    )
                    app_links += 1
    filled = resolve_targets(engine)
    return {"same_engagement": created, "produced": app_links, "resolved": filled}


def _describe(row: Any, relation_type: str, source: str, direction: str) -> dict[str, Any]:
    return {
        "asset_id": row.asset_id,
        "kind": row.kind,
        "name": row.name,
        "title": row.title,
        "summary": row.summary,
        "ref": f"[[{row.kind.lower()}/{row.name}]]",
        "scope": row.scope,
        "type": relation_type,
        "label": RELATION_LABEL.get(relation_type, relation_type),
        "source": source,
        "direction": direction,
    }


def neighbours(engine: Engine, principal: Principal, asset_id: str) -> dict[str, Any]:
    """某份资产的上下游，按当前身份的可见范围过滤。"""
    with engine.connect() as conn:
        out_rows = conn.execute(
            select(asset_relations, assets)
            .join(assets, assets.c.asset_id == asset_relations.c.to_asset_id)
            .where(
                asset_relations.c.from_asset_id == asset_id,
                visibility_clause(principal),
            )
        ).fetchall()
        in_rows = conn.execute(
            select(asset_relations, assets)
            .join(assets, assets.c.asset_id == asset_relations.c.from_asset_id)
            .where(
                asset_relations.c.to_asset_id == asset_id,
                visibility_clause(principal),
            )
        ).fetchall()
        unresolved = conn.execute(
            select(asset_relations.c.to_ref, asset_relations.c.type).where(
                and_(
                    asset_relations.c.from_asset_id == asset_id,
                    asset_relations.c.to_asset_id.is_(None),
                )
            )
        ).fetchall()

    seen: set[tuple[str, str]] = set()
    outgoing = []
    for row in out_rows:
        key = (row.asset_id, row.type)
        if key in seen:
            continue
        seen.add(key)
        outgoing.append(_describe(row, row.type, row.source, "out"))

    incoming = []
    for row in in_rows:
        key = (row.asset_id, row.type)
        if key in seen:
            continue
        seen.add(key)
        incoming.append(_describe(row, row.type, row.source, "in"))

    return {
        "outgoing": sorted(outgoing, key=lambda item: (item["type"], item["title"])),
        "incoming": sorted(incoming, key=lambda item: (item["type"], item["title"])),
        # 引用了但对方还没入库（或我看不到），照实说，不要装作没有
        "unresolved": [{"ref": row.to_ref, "type": row.type} for row in unresolved if row.to_ref],
    }
