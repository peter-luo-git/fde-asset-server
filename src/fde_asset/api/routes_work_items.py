"""工作项与项目的资产关联：SOP 关联到步骤、参考资产、步骤摘要。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import and_, select, update

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.api.work_item_models import WorkItemAssetLink
from fde_asset.api.work_item_access import require_binding_project, require_work_item
from fde_asset.core.db import work_item_assets
from fde_asset.modules.asset import catalog, sop
from fde_asset.platform.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["work-items"])


@router.post("/work-items/{work_item_id}/assets")
def link_asset(
    work_item_id: str,
    payload: WorkItemAssetLink = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    asset_id = payload.asset_id
    role = payload.role
    engagement_slug = require_work_item(context, work_item_id, principal, write=True)
    if payload.engagement_slug and payload.engagement_slug != engagement_slug:
        raise HTTPException(status_code=403, detail="任务不属于指定项目")
    step = payload.sop_step

    asset = catalog.get_asset(context.engine, principal, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    if role == "sop_step":
        if asset["kind"] != "Sop":
            raise HTTPException(status_code=422, detail="只有 SOP 可以按步骤关联")
        if step is None or not any(
            item["number"] == step for item in sop.merged_steps(context.engine, asset_id)["steps"]
        ):
            raise HTTPException(status_code=422, detail="必须指定实际存在的 sop_step")
        with context.engine.connect() as conn:
            existing = conn.execute(
                select(work_item_assets).where(
                    and_(
                        work_item_assets.c.work_item_id == work_item_id,
                        work_item_assets.c.role == "sop_step",
                    )
                )
            ).first()
        # 不再因为"已经开始过会话"而锁住：一个任务往往要 Agent 干好几轮，
        # 做完一步要能换到下一步；每次执行带的是当时绑着的那一步
        if existing:
            require_binding_project(existing, engagement_slug)
            with context.engine.begin() as conn:
                conn.execute(
                    update(work_item_assets)
                    .where(work_item_assets.c.id == existing.id)
                    .values(asset_id=asset_id, sop_step=step, engagement_slug=engagement_slug)
                )
            return {
                "work_item_id": work_item_id,
                "asset_id": asset_id,
                "role": role,
                "sop_step": step,
                "replaced": True,
            }

    with context.engine.begin() as conn:
        conn.execute(
            work_item_assets.insert().values(
                engagement_slug=engagement_slug,
                work_item_id=work_item_id,
                asset_id=asset_id,
                role=role,
                sop_step=step,
                created_by=principal.user_id,
            )
        )
    return {"work_item_id": work_item_id, "asset_id": asset_id, "role": role, "sop_step": step}


@router.delete("/work-items/{work_item_id}/sop-step")
def unlink_sop_step(
    work_item_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """这个任务不再按流程走。只有项目成员（或管理员）能解。"""
    engagement_slug = require_work_item(context, work_item_id, principal, write=True)
    with context.engine.begin() as conn:
        row = conn.execute(
            select(work_item_assets).where(
                and_(
                    work_item_assets.c.work_item_id == work_item_id,
                    work_item_assets.c.role == "sop_step",
                )
            )
        ).first()
        if row is None:
            return {"work_item_id": work_item_id, "removed": False}
        require_binding_project(row, engagement_slug)
        conn.execute(work_item_assets.delete().where(work_item_assets.c.id == row.id))
    return {"work_item_id": work_item_id, "removed": True}


@router.get("/work-items/{work_item_id}/assets")
def list_links(
    work_item_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    engagement_slug = require_work_item(context, work_item_id, principal)
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(work_item_assets).where(work_item_assets.c.work_item_id == work_item_id)
        ).fetchall()
    items = []
    for row in rows:
        require_binding_project(row, engagement_slug)
        asset = catalog.get_asset(context.engine, principal, row.asset_id)
        if asset is None:
            continue
        items.append(
            {
                "asset_id": row.asset_id,
                "role": row.role,
                "sop_step": row.sop_step,
                "locked": row.locked,
                "title": asset["title"],
                "kind": asset["kind"],
            }
        )
    return {"items": items}


@router.get("/work-items/{work_item_id}/sop-step-summary")
def sop_step_summary(
    work_item_id: str,
    lock: bool = False,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """给 orchestrator 拼提示词用：只返回当前步骤的摘要与检查点。"""
    engagement_slug = require_work_item(context, work_item_id, principal, write=lock)
    with context.engine.connect() as conn:
        row = conn.execute(
            select(work_item_assets).where(
                and_(
                    work_item_assets.c.work_item_id == work_item_id,
                    work_item_assets.c.role == "sop_step",
                )
            )
        ).first()
    if row is None:
        return {"found": False, "work_item_id": work_item_id}
    require_binding_project(row, engagement_slug)
    if catalog.get_asset(context.engine, principal, row.asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    if lock:
        with context.engine.begin() as conn:
            conn.execute(
                update(work_item_assets).where(work_item_assets.c.id == row.id).values(locked=True)
            )
    summary = sop.step_summary(context.engine, row.asset_id, row.sop_step or 1)
    summary["work_item_id"] = work_item_id
    summary["locked"] = True if lock else row.locked
    return summary
