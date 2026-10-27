"""工作项的资产：按流程执行（绑到某一步、检查点状态、推进）和参考资产。

SOP 只用来执行，不能当参考资产关联（2026-10-25 定）；想让 Agent 参考某种做法，关联方案或知识。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import and_, select

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.api.work_item_models import SopCheckDecision, WorkItemAssetLink
from fde_asset.api.work_item_access import require_binding_project, require_work_item
from fde_asset.core.db import work_item_assets
from fde_asset.modules.asset import catalog
from fde_asset.modules.asset import sop_progress
from fde_asset.modules.asset.sop_progress import SopProgressError
from fde_asset.platform.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["work-items"])


def _raise(error: SopProgressError) -> None:
    detail: Any = {"code": error.code, "message": str(error)} if error.code else str(error)
    raise HTTPException(status_code=error.status, detail=detail) from None


def apply_default(context: ServiceContext, work_item_id: str, engagement_slug: str) -> None:
    """默认流程设好之后新建的任务，第一次被看到或执行时自动绑上。"""
    lookup = getattr(context.directory, "work_item_created_at", None)
    created_at = lookup(work_item_id) if callable(lookup) else None
    if created_at is None:
        return
    try:
        engagement = (context.directory.engagements() or {}).get(engagement_slug, {})
    except Exception:  # noqa: BLE001 - 拿不到项目信息就只按项目和全公司找
        engagement = {}
    sop_progress.ensure_default(
        context.engine,
        work_item_id=work_item_id,
        engagement_slug=engagement_slug,
        engagement=engagement,
        created_at=created_at,
    )


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

    asset = catalog.get_asset(context.engine, principal, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    if role == "sop_step":
        if asset["kind"] != "Sop":
            raise HTTPException(status_code=422, detail="只有 SOP 可以按步骤关联")
        if payload.sop_step is None:
            raise HTTPException(status_code=422, detail="必须指定实际存在的 sop_step")
        with context.engine.connect() as conn:
            existing = sop_progress.binding_row(conn, work_item_id)
            if sop_progress.usable_asset(conn, asset_id) is None:
                raise HTTPException(status_code=422, detail="这条流程还是草稿、已下架或没通过校验")
        if existing:
            require_binding_project(existing, engagement_slug)
        # 不因为"已经开始过会话"而锁住：一个任务往往要 Agent 干好几轮，做完一步要能换到下一步
        try:
            return {
                **sop_progress.bind(
                    context.engine,
                    work_item_id=work_item_id,
                    engagement_slug=engagement_slug,
                    asset_id=asset_id,
                    step=payload.sop_step,
                    actor=principal.user_id,
                    force=payload.force,
                ),
                "role": role,
            }
        except SopProgressError as error:
            _raise(error)
    if asset["kind"] == "Sop":
        raise HTTPException(
            status_code=422,
            detail="SOP 只用于按步骤执行任务，不能当参考资产；想让 Agent 参考某种做法，请关联方案或知识",
        )
    with context.engine.begin() as conn:
        exists = conn.execute(
            select(work_item_assets.c.id).where(
                and_(
                    work_item_assets.c.work_item_id == work_item_id,
                    work_item_assets.c.asset_id == asset_id,
                    work_item_assets.c.role == role,
                )
            )
        ).first()
        if exists is None:
            conn.execute(
                work_item_assets.insert().values(
                    engagement_slug=engagement_slug,
                    work_item_id=work_item_id,
                    asset_id=asset_id,
                    role=role,
                    created_by=principal.user_id,
                )
            )
    return {"work_item_id": work_item_id, "asset_id": asset_id, "role": role, "sop_step": None}


@router.delete("/work-items/{work_item_id}/sop-step")
def unlink_sop_step(
    work_item_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """这个任务不按流程做。记下来，项目设了默认流程也不再自动绑回来。"""
    engagement_slug = require_work_item(context, work_item_id, principal, write=True)
    with context.engine.connect() as conn:
        row = sop_progress.binding_row(conn, work_item_id)
    if row is not None:
        require_binding_project(row, engagement_slug)
    removed = sop_progress.opt_out(
        context.engine,
        work_item_id=work_item_id,
        engagement_slug=engagement_slug,
        actor=principal.user_id,
    )
    return {"work_item_id": work_item_id, "removed": removed}


@router.get("/work-items/{work_item_id}/assets")
def list_links(
    work_item_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    engagement_slug = require_work_item(context, work_item_id, principal)
    apply_default(context, work_item_id, engagement_slug)
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(work_item_assets).where(
                work_item_assets.c.work_item_id == work_item_id,
                work_item_assets.c.role != "sop_none",
            )
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


@router.get("/engagements/{engagement_slug}/sop-board")
def engagement_sop_board(
    engagement_slug: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """项目看板：每个按流程执行的任务做到第几步（卡片上的流程进度标记）。"""
    if not principal.is_admin and engagement_slug not in principal.engagement_slugs:
        raise HTTPException(status_code=404, detail="项目不存在或无权访问")
    return {"items": sop_progress.board_summaries(context.engine, engagement_slug)}


@router.get("/work-items/{work_item_id}/sop")
def work_item_sop(
    work_item_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """任务页「执行规范」：按哪条流程、在第几步、每个检查点的状态和证据。"""
    engagement_slug = require_work_item(context, work_item_id, principal)
    apply_default(context, work_item_id, engagement_slug)
    with context.engine.connect() as conn:
        row = sop_progress.binding_row(conn, work_item_id)
        opted_out = sop_progress.binding_row(conn, work_item_id, "sop_none") is not None
    if row is None:
        return {"bound": False, "opted_out": opted_out, "work_item_id": work_item_id}
    require_binding_project(row, engagement_slug)
    if catalog.get_asset(context.engine, principal, row.asset_id) is None:
        return {
            "bound": True,
            "hidden": True,
            "work_item_id": work_item_id,
            "asset_id": row.asset_id,
        }
    return {"work_item_id": work_item_id, **sop_progress.public_state(context.engine, row)}


@router.post("/work-items/{work_item_id}/sop/checks")
def decide_check(
    work_item_id: str,
    payload: SopCheckDecision = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """人确认、退回或清空一个检查点。项目里能改任务的人都能做，记下是谁。"""
    engagement_slug = require_work_item(context, work_item_id, principal, write=True)
    with context.engine.connect() as conn:
        row = sop_progress.binding_row(conn, work_item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="这个任务没按流程执行")
    require_binding_project(row, engagement_slug)
    try:
        return sop_progress.human_decide(
            context.engine,
            work_item_id=work_item_id,
            step=payload.step,
            index=payload.index,
            decision=payload.decision,
            note=payload.note,
            actor=principal.user_id,
        )
    except SopProgressError as error:
        _raise(error)


@router.post("/work-items/{work_item_id}/sop/refresh")
def refresh_sop(
    work_item_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """流程出了新版本：换成新版本的步骤，文字没变的检查点保留状态。"""
    engagement_slug = require_work_item(context, work_item_id, principal, write=True)
    with context.engine.connect() as conn:
        row = sop_progress.binding_row(conn, work_item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="这个任务没按流程执行")
    require_binding_project(row, engagement_slug)
    try:
        return sop_progress.refresh_version(
            context.engine, work_item_id=work_item_id, actor=principal.user_id
        )
    except SopProgressError as error:
        _raise(error)


@router.get("/work-items/{work_item_id}/sop-step-summary")
def sop_step_summary(
    work_item_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """给人看的当前步骤说明（和 Agent 拿到的是同一份）。"""
    engagement_slug = require_work_item(context, work_item_id, principal)
    with context.engine.connect() as conn:
        row = sop_progress.binding_row(conn, work_item_id)
    if row is None:
        return {"found": False, "work_item_id": work_item_id}
    require_binding_project(row, engagement_slug)
    asset = catalog.get_asset(context.engine, principal, row.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    summary = sop_progress.agent_step(context.engine, row, asset["title"])
    return {**summary, "work_item_id": work_item_id, "asset_id": row.asset_id}
