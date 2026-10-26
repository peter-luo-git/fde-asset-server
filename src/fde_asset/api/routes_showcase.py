"""资产示范库接口：列出 FCP 预置的示范资产、管理员一键安装；任何看得到的资产都能复制为草稿。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.api.routes_harvest import _scan_context
from fde_asset.core.db import assets
from fde_asset.modules.asset import catalog, showcase
from fde_asset.modules.asset.indexer import finish_index, index_repository
from fde_asset.modules.harvest import service as harvest
from fde_asset.platform.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["showcase"])


@router.get("/showcase")
def showcase_listing(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """示范资产清单：每份是什么、怎么试、装了没有（装了给出资产编号）。"""
    return {"items": showcase.listing(context.engine), "can_install": principal.is_admin}


@router.post("/showcase/install")
def showcase_install(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """管理员安装示范资产（全公司可见）：没装的新装，平台更新过内容的升级版本，其余跳过。"""
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="只有管理员能安装示范资产")
    target = context.repo_for("company")
    result = showcase.install(
        context.engine,
        principal,
        context.repo_port,
        target_repo=target,
        scan_context=_scan_context(context),
    )
    if result["installed"] or result["updated"]:
        index_repository(
            context.engine,
            context.repo_port,
            target,
            text_limit=context.settings.index_text_limit,
            file_store=context.file_store,
        )
        finish_index(context.engine, owner_of=context.target_owner)
    return {**result, "items": showcase.listing(context.engine)}


@router.post("/assets/{asset_id}/copy")
def copy_asset(
    asset_id: str,
    payload: dict[str, Any] = Body(default={}),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """复制为草稿：整份目录复制成新草稿，换新标识，在上面改成自己的资产。"""
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    with context.engine.connect() as conn:
        row = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
    if row is None or row.kind == "Application":
        raise HTTPException(status_code=422, detail="这份资产不能复制")
    default_scope = "user" if row.kind == "Skill" else "department"
    try:
        return showcase.copy_as_draft(
            context.engine,
            principal,
            context.repo_port,
            row,
            scope=str(payload.get("scope") or default_scope),
            department_code=str(payload.get("department_code") or ""),
            engagement_slug=str(payload.get("engagement_slug") or ""),
            customer_code=str(payload.get("customer_code") or ""),
            title=str(payload.get("title") or ""),
        )
    except harvest.HarvestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
