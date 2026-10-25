"""技能市场：公开技能库的来源、目录、安装、更新；以及"这个 Agent 实际会带哪些技能"。

来源只有管理员能加删；谁都能浏览和安装（装进来的是草稿，要公司资产评审员评过才入库）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.core.db import assets
from fde_asset.modules.asset import catalog as catalog_module
from fde_asset.modules.asset import skill_sources
from fde_asset.modules.harvest import service as harvest
from fde_asset.platform.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["skill-market"])


def _cache(context: ServiceContext):
    return context.settings.root / "skill-sources"


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(status_code=getattr(exc, "status", 422), detail=str(exc))


@router.get("/skill-market/sources")
def sources(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return {"items": skill_sources.listing(context.engine), "can_manage": principal.is_admin}


@router.post("/skill-market/sources")
def add_source(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="只有管理员能加技能来源")
    try:
        return skill_sources.add(
            context.engine,
            name=str(payload.get("name") or ""),
            url=str(payload.get("url") or ""),
            branch=str(payload.get("branch") or ""),
            actor=principal.user_id,
        )
    except skill_sources.SkillSourceError as exc:
        raise _fail(exc) from None


@router.post("/skill-market/sources/{source_id}/remove")
def remove_source(
    source_id: int,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="只有管理员能删技能来源")
    return {"removed": skill_sources.remove(context.engine, source_id, _cache(context))}


@router.post("/skill-market/sources/{source_id}/refresh")
def refresh_source(
    source_id: int,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """从来源拉最新的目录。网络不通返回 status=unreachable 和手动导入的做法。"""
    try:
        view = skill_sources.refresh(
            context.engine, source_id, _cache(context), force=principal.is_admin
        )
    except skill_sources.SkillSourceError as exc:
        raise _fail(exc) from None
    if view["status"] != "ok":
        view["manual_steps"] = skill_sources.manual_steps(view["url"], view["branch"])
    return view


@router.get("/skill-market/sources/{source_id}/catalog")
def source_catalog(
    source_id: int,
    q: str = Query("", max_length=100),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    try:
        return skill_sources.catalog(context.engine, source_id, q)
    except skill_sources.SkillSourceError as exc:
        raise _fail(exc) from None


@router.post("/skill-market/install")
def install(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """装进草稿：选归属范围（默认个人专用）。之后在草稿页看检查结果、补信息、提交评审。"""
    try:
        package, source = skill_sources.package_for(
            context.engine,
            int(payload.get("source_id") or 0),
            str(payload.get("path") or ""),
            _cache(context),
        )
        return harvest.draft_from_skill_package(
            context.engine,
            principal,
            package,
            title=str(payload.get("title") or ""),
            scope=str(payload.get("scope") or "user"),
            department_code=str(payload.get("department_code") or ""),
            engagement_slug=str(payload.get("engagement_slug") or ""),
            customer_code=str(payload.get("customer_code") or ""),
            origin="install",
            source=source,
        )
    except (skill_sources.SkillSourceError, harvest.HarvestError, ValueError) as exc:
        raise _fail(exc) from None


@router.get("/skill-market/assets/{asset_id}/update")
def update_status(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """从技能库装的技能：来源里有没有新版本（按目录内容比，以最近一次刷新为准）。"""
    asset = catalog_module.get_asset(context.engine, principal, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    with context.engine.connect() as conn:
        row = conn.execute(
            select(assets.c.source_json).where(assets.c.asset_id == asset_id)
        ).first()
    source = json.loads(row.source_json or "{}") if row else {}
    if not source.get("url"):
        return {"installed": False}
    match = next(
        (item for item in skill_sources.listing(context.engine) if item["url"] == source["url"]),
        None,
    )
    entry = None
    if match is not None:
        listed = skill_sources.catalog(context.engine, match["id"])
        entry = next((e for e in listed["entries"] if e["path"] == source.get("path", "")), None)
    return {
        "installed": True,
        "source": {
            k: source.get(k, "") for k in ("url", "path", "commit", "license", "sourceName")
        },
        "source_id": match["id"] if match else None,
        "checked_at": match["checked_at"] if match else "",
        "outdated": bool(entry) and entry["digest"] != source.get("digest"),
        "latest_commit": match["commit_sha"] if match else "",
        "removed_from_source": match is not None and match["status"] == "ok" and entry is None,
    }


@router.post("/skill-market/assets/{asset_id}/update")
def update_installed(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """来源有新版本：生成修订草稿（编号不变、版本加一），同样要过检查和评审。"""
    status = update_status(asset_id, context, principal)
    if not status.get("installed") or not status.get("source_id"):
        raise HTTPException(status_code=422, detail="这个技能不是从已配置的技能库装的")
    if not status.get("outdated"):
        raise HTTPException(status_code=409, detail="来源里没有新版本")
    with context.engine.connect() as conn:
        row = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
    try:
        package, source = skill_sources.package_for(
            context.engine, status["source_id"], status["source"]["path"], _cache(context)
        )
        return harvest.draft_from_skill_package(
            context.engine,
            principal,
            package,
            title=row.title,
            scope=row.scope,
            department_code=row.department_code or "",
            engagement_slug=row.engagement_slug or "",
            customer_code=row.customer_code or "",
            origin="install",
            source=source,
            revises_asset_id=asset_id,
        )
    except (skill_sources.SkillSourceError, harvest.HarvestError) as exc:
        raise _fail(exc) from None


@router.get("/agent-skills")
def agent_skills(
    engagement_slug: str = Query(..., min_length=1),
    agent_key: str = Query(""),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """这个 Agent 在这个项目里实际会带哪些技能、各自为什么带（项目自己的、关联的、必选的、个人的）。"""
    from fde_asset.core.db import target_assets
    from fde_asset.modules.asset import mandatory
    from fde_asset.modules.asset.snapshot import select_assets

    if not principal.is_admin and engagement_slug not in principal.engagement_slugs:
        raise HTTPException(status_code=404, detail="项目不存在或无权访问")
    project = (context.directory.engagements() or {}).get(engagement_slug, {})
    membership = next(
        (m for m in principal.memberships if m.engagement_slug == engagement_slug), None
    )
    department = str(project.get("department_code", "")) or (
        membership.department_code if membership else ""
    )
    customer = str(project.get("customer_code", "")) or (
        membership.customer_code if membership else ""
    )
    chosen = select_assets(
        context.engine,
        department_code=department,
        customer_code=customer,
        engagement_slug=engagement_slug,
        target_type="engagement",
        target_id=engagement_slug,
        also_targets=(("agent", agent_key),) if agent_key else (),
        username=principal.user_id,
    )
    with context.engine.connect() as conn:
        linked = {
            (row.target_type, row.asset_id)
            for row in conn.execute(
                select(target_assets.c.target_type, target_assets.c.asset_id).where(
                    target_assets.c.target_id.in_([v for v in (engagement_slug, agent_key) if v])
                )
            )
        }
        forced = mandatory.ids_for(
            conn,
            department_code=department,
            customer_code=customer,
            engagement_slug=engagement_slug,
            username=principal.user_id,
        )
    items = []
    for row in chosen.rows:
        if row.kind != "Skill":
            continue
        reasons = []
        if row.scope == "engagement":
            reasons.append("项目自己的")
        if ("engagement", row.asset_id) in linked:
            reasons.append("关联给项目")
        if ("agent", row.asset_id) in linked:
            reasons.append("关联给这个 Agent")
        if row.asset_id in forced:
            reasons.append("必选")
        if row.scope == "user":
            reasons.append("你的个人技能（只在你的私聊里带）")
        items.append(
            {
                "asset_id": row.asset_id,
                "name": chosen.mounted.get(row.asset_id, row.name),
                "title": row.title,
                "summary": row.summary,
                "version": row.version,
                "reasons": reasons,
            }
        )
    return {"items": items}
