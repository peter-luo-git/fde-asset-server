"""资产目录查询：筛选、搜索、排序，可见性全部下推到 SQL。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.engine import Engine

from fde_asset.core.db import asset_references, asset_usages, assets
from fde_asset.modules.asset.manifest import SHORT_BY_KIND
from fde_asset.modules.asset.visibility import visibility_clause
from fde_asset.platform.identity import Principal


@dataclass
class CatalogQuery:
    kind: str | None = None
    #: 不要这几类。资产目录用它把应用挡在外面——应用有自己的页面（应用市场）
    exclude_kinds: tuple[str, ...] = ()
    scope: str | None = None
    industry: str | None = None
    owner_department: str | None = None
    #: 精确到某一个负责人，形如 user:chen 或 department:data-intel
    owner: str | None = None
    #: 只看个人负责的还是部门负责的：user | department
    owner_kind: str | None = None
    #: 命中其中任意一个负责人即可（「我负责的」= 我本人 + 我部门）
    owner_any: list[str] | None = None
    lifecycle: str | None = None
    quality: str | None = None
    nature: str | None = None
    q: str | None = None
    include_deprecated: bool = False
    sort: str = "updated"  # updated | reuse | title
    limit: int = 50
    offset: int = 0


def row_to_dict(row: Any, reuse: int = 0, usage: int = 0) -> dict[str, Any]:
    data = dict(row._mapping)
    for field_name in (
        "applicability_json",
        "tags_json",
        "industry_json",
        "source_json",
        "kind_spec_json",
        "attachments_json",
    ):
        raw = data.pop(field_name)
        data[field_name.removesuffix("_json")] = json.loads(
            raw or ("{}" if field_name.endswith("y_json") else "[]")
        )
    data["ref"] = f"[[{SHORT_BY_KIND.get(data['kind'], data['kind'].lower())}/{data['name']}]]"
    data["usage_count"] = usage
    data["reuse_engagement_count"] = reuse
    for key in ("created_at", "updated_at", "deleted_at"):
        if data.get(key) is not None:
            data[key] = data[key].isoformat()
    return data


def base_select(principal: Principal, query: CatalogQuery) -> Select:
    statement = select(assets).where(visibility_clause(principal), assets.c.valid.is_(True))
    if query.kind:
        statement = statement.where(assets.c.kind == query.kind)
    if query.exclude_kinds:
        statement = statement.where(assets.c.kind.notin_(query.exclude_kinds))
    if query.scope:
        statement = statement.where(assets.c.scope == query.scope)
    if query.industry:
        statement = statement.where(assets.c.industry_json.contains(f'"{query.industry}"'))
    if query.owner_department:
        statement = statement.where(
            and_(
                assets.c.owner_kind == "department", assets.c.owner_value == query.owner_department
            )
        )
    if query.owner:
        statement = statement.where(assets.c.owner_ref == query.owner)
    if query.owner_kind:
        statement = statement.where(assets.c.owner_kind == query.owner_kind)
    if query.owner_any:
        statement = statement.where(assets.c.owner_ref.in_(query.owner_any))
    if query.lifecycle:
        statement = statement.where(assets.c.lifecycle == query.lifecycle)
    elif not query.include_deprecated:
        statement = statement.where(assets.c.lifecycle.notin_(["deprecated", "archived", "draft"]))
    if query.quality:
        statement = statement.where(assets.c.quality == query.quality)
    if query.nature:
        statement = statement.where(assets.c.nature == query.nature)
    if query.q:
        like = f"%{query.q}%"
        statement = statement.where(
            or_(
                assets.c.name.like(like),
                assets.c.title.like(like),
                assets.c.summary.like(like),
                assets.c.tags_json.like(like),
                assets.c.content_text.like(like),
            )
        )
    return statement


def search(engine: Engine, principal: Principal, query: CatalogQuery) -> dict[str, Any]:
    statement = base_select(principal, query)
    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
        reuse_map = reuse_counts(conn)
        usage_map = _usage_counts(conn)
        if query.sort == "title":
            statement = statement.order_by(assets.c.title)
        else:
            statement = statement.order_by(assets.c.updated_at.desc())
        rows = conn.execute(statement.limit(query.limit).offset(query.offset)).fetchall()
    items = [
        row_to_dict(r, reuse_map.get(r.asset_id, 0), usage_map.get(r.asset_id, 0)) for r in rows
    ]
    if query.sort == "reuse":
        items.sort(key=lambda item: item["reuse_engagement_count"], reverse=True)
    return {"total": total, "items": items}


def get_asset(engine: Engine, principal: Principal, asset_id: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(
            select(assets).where(assets.c.asset_id == asset_id, visibility_clause(principal))
        ).first()
        if row is None:
            return None
        reuse = reuse_counts(conn).get(asset_id, 0)
        usage = _usage_counts(conn).get(asset_id, 0)
    return row_to_dict(row, reuse, usage)


def resolve_ref(
    engine: Engine, principal: Principal, short_kind: str, name: str, version: str = ""
) -> dict[str, Any]:
    """解析 [[kind/name]]：无权限或不存在返回同一种占位，避免被用来探测资产是否存在。"""
    from fde_asset.modules.asset.manifest import KIND_BY_SHORT

    kind = KIND_BY_SHORT.get(short_kind)
    placeholder = {"status": "asset_not_visible", "ref": f"[[{short_kind}/{name}]]"}
    if kind is None:
        return placeholder
    with engine.connect() as conn:
        row = conn.execute(
            select(assets)
            .where(
                assets.c.kind == kind,
                assets.c.name == name,
                assets.c.valid.is_(True),
                visibility_clause(principal),
            )
            .order_by(assets.c.scope.desc())
        ).first()
    if row is None:
        return placeholder
    return {
        "status": "ok",
        "ref": f"[[{short_kind}/{name}]]",
        "asset_id": row.asset_id,
        "kind": row.kind,
        "title": row.title,
        "summary": row.summary,
        "lifecycle": row.lifecycle,
        "quality": row.quality,
        "scope": row.scope,
        "version": row.version,
        "requested_version": version,
    }


def reuse_counts(conn) -> dict[str, int]:
    """复用项目数 = 来源项目以外的不同项目数（使用 + 引用）。"""
    counts: dict[str, set[str]] = {}
    source_map: dict[str, str] = {}
    for row in conn.execute(
        select(assets.c.asset_id, assets.c.source_json, assets.c.engagement_slug)
    ):
        source = json.loads(row.source_json or "{}")
        source_map[row.asset_id] = source.get("engagementSlug") or row.engagement_slug or ""
    for row in conn.execute(select(asset_usages.c.asset_id, asset_usages.c.engagement_slug)):
        if row.engagement_slug and row.engagement_slug != source_map.get(row.asset_id, ""):
            counts.setdefault(row.asset_id, set()).add(row.engagement_slug)
    for row in conn.execute(
        select(asset_references.c.asset_id, asset_references.c.engagement_slug)
    ):
        if row.engagement_slug and row.engagement_slug != source_map.get(row.asset_id, ""):
            counts.setdefault(row.asset_id, set()).add(row.engagement_slug)
    return {asset_id: len(slugs) for asset_id, slugs in counts.items()}


def _usage_counts(conn) -> dict[str, int]:
    rows = conn.execute(
        select(asset_usages.c.asset_id, func.count()).group_by(asset_usages.c.asset_id)
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def passport(engine: Engine, principal: Principal, asset_id: str) -> dict[str, Any] | None:
    """资产护照：出生地 + 每个用过它的项目一枚印章 + 统计。"""
    asset = get_asset(engine, principal, asset_id)
    if asset is None:
        return None
    with engine.connect() as conn:
        stamps: dict[str, dict[str, Any]] = {}
        for row in conn.execute(
            select(asset_usages)
            .where(asset_usages.c.asset_id == asset_id)
            .order_by(asset_usages.c.created_at)
        ):
            slug = row.engagement_slug or "(未知项目)"
            stamp = stamps.setdefault(
                slug,
                {
                    "engagement_slug": slug,
                    "first_used_at": row.created_at.isoformat(),
                    "events": 0,
                    "delivered_work_items": set(),
                },
            )
            stamp["events"] += 1
            if row.delivered_pr and row.work_item_id:
                stamp["delivered_work_items"].add(row.work_item_id)
        references = conn.execute(
            select(func.count())
            .select_from(asset_references)
            .where(asset_references.c.asset_id == asset_id)
        ).scalar_one()
    helped = set()
    for stamp in stamps.values():
        helped |= stamp["delivered_work_items"]
        stamp["delivered_work_items"] = len(stamp["delivered_work_items"])
    source = asset["source"]
    return {
        "asset_id": asset_id,
        "birthplace": {
            "customer_code": source.get("customerCode", ""),
            "engagement_slug": source.get("engagementSlug", ""),
            "origin": source.get("origin", ""),
            "note": source.get("note", ""),
        },
        "stamps": sorted(stamps.values(), key=lambda s: s["first_used_at"]),
        "stats": {
            "helped_work_items": len(helped),
            "reference_count": references,
            "reuse_engagement_count": asset["reuse_engagement_count"],
            "version": asset["version"],
        },
    }
