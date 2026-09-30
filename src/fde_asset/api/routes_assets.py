"""资产目录、详情、引用、SOP、快照、使用事件接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.core.db import asset_index_findings
from fde_asset.modules.asset import catalog, snapshot, sop, usage
from fde_asset.modules.asset.indexer import index_all
from fde_asset.platform.identity import Principal
from fde_asset.platform.refs.wiki import parse_refs

router = APIRouter(prefix="/api/v1", tags=["assets"])


@router.get("/assets")
def list_assets(
    kind: str | None = None,
    scope: str | None = None,
    industry: str | None = None,
    owner_department: str | None = None,
    lifecycle: str | None = None,
    quality: str | None = None,
    nature: str | None = None,
    q: str | None = None,
    sort: str = "updated",
    include_deprecated: bool = False,
    limit: int = Query(50, le=200),
    offset: int = 0,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    query = catalog.CatalogQuery(
        kind=kind,
        scope=scope,
        industry=industry,
        owner_department=owner_department,
        lifecycle=lifecycle,
        quality=quality,
        nature=nature,
        q=q,
        sort=sort,
        include_deprecated=include_deprecated,
        limit=limit,
        offset=offset,
    )
    return catalog.search(context.engine, principal, query)


@router.get("/assets/invalid")
def list_invalid(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not (principal.is_admin or principal.is_asset_reviewer):
        raise HTTPException(status_code=403, detail="只有管理员或资产评审员可以查看无效资产")
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(asset_index_findings).order_by(asset_index_findings.c.id.desc())
        ).fetchall()
    return {
        "items": [dict(row._mapping) | {"detected_at": row.detected_at.isoformat()} for row in rows]
    }


@router.get("/assets/resolve")
def resolve(
    ref: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    refs = parse_refs(ref if ref.startswith("[[") else f"[[{ref}]]")
    if not refs:
        return {"status": "asset_not_visible", "ref": ref}
    first = refs[0]
    return catalog.resolve_ref(
        context.engine, principal, first.short_kind, first.name, first.version
    )


@router.post("/assets/references")
def add_references(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """解析一段文本里的 [[引用]] 并记录（频道消息、工作项描述、交付摘要共用）。"""
    text = payload.get("text", "")
    source_type = payload.get("source_type", "message")
    source_id = str(payload.get("source_id", ""))
    engagement_slug = payload.get("engagement_slug", "")
    recorded, skipped = [], []
    for ref in parse_refs(text):
        card = catalog.resolve_ref(context.engine, principal, ref.short_kind, ref.name, ref.version)
        if card["status"] != "ok":
            skipped.append(ref.text)
            continue
        created = usage.record_reference(
            context.engine,
            asset_id=card["asset_id"],
            source_type=source_type,
            source_id=f"{source_id}:{ref.name}",
            engagement_slug=engagement_slug,
            created_by=principal.user_id,
            asset_version=card.get("version", ""),
        )
        recorded.append({"ref": ref.text, "asset_id": card["asset_id"], "new": created})
    return {"recorded": recorded, "skipped": skipped}


@router.get("/assets/{asset_id}")
def get_asset(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    data = catalog.get_asset(context.engine, principal, asset_id)
    if data is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    return data


@router.get("/assets/{asset_id}/passport")
def get_passport(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    data = catalog.passport(context.engine, principal, asset_id)
    if data is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    return data


@router.get("/assets/{asset_id}/sop-steps")
def get_sop_steps(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    return sop.merged_steps(context.engine, asset_id)


@router.get("/assets/{asset_id}/sop-steps/{step_number}")
def get_sop_step(
    asset_id: str,
    step_number: int,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    return sop.step_summary(context.engine, asset_id, step_number)


@router.post("/assets/usages")
def post_usages(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    items = [usage.UsageInput(**item) for item in payload.get("items", [])]
    return usage.record_usages(context.engine, items)


@router.post("/snapshots")
def build_snapshot(
    payload: dict[str, Any] = Body(default={}),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    result = snapshot.build_snapshot(
        context.engine,
        context.repo_port,
        snapshot_root=context.settings.snapshot_dir,
        department_code=payload.get("department_code", ""),
        engagement_slug=payload.get("engagement_slug", ""),
        index_limit=context.settings.knowledge_index_limit,
        industries=tuple(payload.get("industries", [])),
    )
    return {
        "sha": result.sha,
        "path": str(result.path),
        "reused": result.reused,
        "counts": result.counts,
        "index_bytes": result.index_bytes,
        "index_truncated": result.index_truncated,
        "skipped": result.skipped,
    }


@router.post("/admin/assets/reindex")
def reindex(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not (principal.is_admin or principal.is_asset_reviewer):
        raise HTTPException(status_code=403, detail="只有管理员或资产评审员可以触发重建索引")
    reports = index_all(
        context.engine,
        context.repo_port,
        context.repos(),
        text_limit=context.settings.index_text_limit,
    )
    return {
        "repos": [
            {
                "repo": r.repo,
                "head": r.head,
                "indexed": r.indexed,
                "invalid": r.invalid,
                "removed": r.removed,
            }
            for r in reports
        ]
    }
