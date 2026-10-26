"""SOP 接口：默认流程（谁能设、设在哪个范围）、编写时的预览与检查、可选的上层流程。

默认流程的权限和必选资产一样：管理员，以及这个范围的负责人（部门主管、客户下任一项目的负责人、
项目负责人）。个人没有默认流程——流程是给任务用的，任务属于项目。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.api.routes_mandatory import _scope_context
from fde_asset.api.work_item_access import require_work_item
from fde_asset.core.db import assets, sop_defaults
from fde_asset.modules.asset import catalog, mandatory, sop, sop_progress
from fde_asset.modules.asset.visibility import visibility_clause
from fde_asset.platform.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["sop"])

#: 每一层能继承哪一层
PARENT_LAYER = {"L2": "L1", "L3": "L2"}


def _entry(scope: str, code: str) -> str:
    return "company" if scope == "company" else f"{scope}:{code}"


def _parse_default_entry(entry: str) -> tuple[str, str]:
    try:
        scope, code = mandatory.parse(entry)
    except mandatory.MandatoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if scope == "user":
        raise HTTPException(status_code=422, detail="默认流程只能设给全公司、部门、客户或项目")
    return scope, code


def _layer(row: Any) -> str:
    return json.loads(row.kind_spec_json or "{}").get("layer", "")


def _view(conn: Any, principal: Principal, row: Any) -> dict[str, Any]:
    asset = conn.execute(select(assets).where(assets.c.asset_id == row.asset_id)).first()
    usable = sop_progress.usable_asset(conn, row.asset_id) is not None
    return {
        "entry": _entry(row.scope, row.code),
        "scope": row.scope,
        "code": row.code,
        "asset_id": row.asset_id,
        "title": asset.title if asset else "（流程已不存在）",
        "layer": _layer(asset) if asset else "",
        "effective": usable,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "can_remove": mandatory.may_manage(principal, row.scope, row.code),
    }


@router.get("/sop-defaults")
def list_defaults(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """默认流程清单：管理员看全部，其他人看自己负责的范围。"""
    mine = {
        mandatory.parse(key)
        for key in mandatory.manageable_scopes(principal)
        if not key.startswith("user")
    }
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(sop_defaults).order_by(sop_defaults.c.scope, sop_defaults.c.code)
        ).fetchall()
        items = [
            _view(conn, principal, row)
            for row in rows
            if principal.is_admin or (row.scope, row.code) in mine
        ]
    return {
        "items": items,
        "manageable": [
            k for k in mandatory.manageable_scopes(principal) if not k.startswith("user")
        ],
        "is_admin": principal.is_admin,
    }


@router.put("/sop-defaults")
def put_default(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """设（或换）某个范围的默认流程。之后在这个范围里新建的任务自动按它执行。"""
    entry = str(payload.get("entry", ""))
    asset_id = str(payload.get("asset_id", ""))
    scope, code = _parse_default_entry(entry)
    if not mandatory.may_manage(principal, scope, code):
        raise HTTPException(status_code=403, detail="只有管理员或这个范围的负责人能设默认流程")
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    try:
        scope_context = _scope_context(context, principal, entry)
    except mandatory.MandatoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    with context.engine.connect() as conn:
        if sop_progress.usable_asset(conn, asset_id) is None:
            raise HTTPException(
                status_code=422, detail="只能选校验通过、不是草稿或下架的 SOP 作为默认流程"
            )
        if not mandatory._visible_in(conn, asset_id, scope, code, scope_context):
            raise HTTPException(status_code=422, detail="这条流程对这个范围不可见，先调整可见范围")
    if not sop.merged_steps(context.engine, asset_id)["steps"]:
        raise HTTPException(status_code=422, detail="这条流程没有可执行的步骤")
    sop_progress.set_default(
        context.engine, scope=scope, code=code, asset_id=asset_id, actor=principal.user_id
    )
    return {"entry": _entry(scope, code), "asset_id": asset_id}


@router.post("/sop-defaults/remove")
def remove_default(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """取消默认流程。已经绑上的任务不受影响。"""
    scope, code = _parse_default_entry(str(payload.get("entry", "")))
    if not mandatory.may_manage(principal, scope, code):
        raise HTTPException(status_code=403, detail="只有管理员或这个范围的负责人能取消默认流程")
    return {"removed": sop_progress.clear_default(context.engine, scope=scope, code=code)}


@router.get("/engagements/{engagement_slug}/sop-default")
def engagement_default(
    engagement_slug: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """项目设置页：这个项目现在用哪条默认流程（可能继承自客户、部门、全公司），我能不能改。"""
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
    with context.engine.connect() as conn:
        row = sop_progress.default_for(
            conn,
            department_code=department,
            customer_code=customer,
            engagement_slug=engagement_slug,
        )
        current = _view(conn, principal, row) if row is not None else None
    return {
        "engagement_slug": engagement_slug,
        "default": current,
        "inherited": current is not None and current["scope"] != "engagement",
        "can_manage": mandatory.may_manage(principal, "engagement", engagement_slug),
        "entry": f"engagement:{engagement_slug}",
    }


@router.post("/sop/preview")
def preview(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """草稿页实时预览：和上层合起来以后的步骤（每步来自哪一层），以及编写检查的提示。"""
    content = str(payload.get("content", ""))
    if len(content) > 200_000:
        raise HTTPException(status_code=422, detail="内容太长")
    layer = str(payload.get("layer") or "L1")
    if layer not in PARENT_LAYER and layer != "L1":
        raise HTTPException(status_code=422, detail="层级只能是 L1、L2、L3")
    extends = str(payload.get("extends") or "") if layer != "L1" else ""
    if extends:
        parent_name = extends.split("/")[-1]
        with context.engine.connect() as conn:
            visible = conn.execute(
                select(assets.c.asset_id).where(
                    assets.c.kind == "Sop",
                    assets.c.name == parent_name,
                    assets.c.deleted_at.is_(None),
                    visibility_clause(principal),
                )
            ).first()
        if visible is None:
            return {
                "steps": [],
                "layers": [],
                "parent_found": False,
                "findings": sop.lint(content, layer=layer, extends=extends, parent_found=False),
            }
    try:
        return sop.preview(
            context.engine,
            content=content,
            layer=layer,
            extends=extends,
            name=str(payload.get("name") or ""),
            title=str(payload.get("title") or ""),
        )
    except sop.SopCycleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/sop/parents")
def parent_candidates(
    layer: str = Query(..., description="正在写的流程是哪一层：L2 只列 L1，L3 只列 L2"),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    wanted = PARENT_LAYER.get(layer)
    if wanted is None:
        return {"items": []}
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(assets)
            .where(
                assets.c.kind == "Sop",
                assets.c.deleted_at.is_(None),
                assets.c.valid.is_(True),
                assets.c.lifecycle.notin_(["deprecated", "archived"]),
                visibility_clause(principal),
            )
            .order_by(assets.c.title)
        ).fetchall()
    return {
        "items": [
            {"asset_id": row.asset_id, "name": row.name, "title": row.title, "layer": _layer(row)}
            for row in rows
            if _layer(row) == wanted
        ]
    }


@router.get("/sops")
def executable_sops(
    work_item_id: str = Query(
        "", description="给任务选流程时传任务编号，任务所属项目自己的流程排在前面"
    ),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """任务页选流程用：能执行的 SOP，按层级分组（项目自己的 L3 在前，其次 L2、L1）。"""
    engagement_slug = require_work_item(context, work_item_id, principal) if work_item_id else ""
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(assets).where(
                assets.c.kind == "Sop",
                assets.c.deleted_at.is_(None),
                assets.c.valid.is_(True),
                assets.c.lifecycle.notin_(["deprecated", "archived", "draft"]),
                visibility_clause(principal),
            )
        ).fetchall()
    order = {"L3": 0, "L2": 1, "L1": 2}

    def rank(row: Any) -> tuple[int, int, str]:
        own = 0 if engagement_slug and row.engagement_slug == engagement_slug else 1
        return (own, order.get(_layer(row), 3), row.title)

    return {
        "items": [
            {
                "asset_id": row.asset_id,
                "name": row.name,
                "title": row.title,
                "layer": _layer(row),
                "scope": row.scope,
                "own_project": bool(engagement_slug) and row.engagement_slug == engagement_slug,
                "summary": row.summary,
            }
            for row in sorted(rows, key=rank)
        ]
    }


@router.get("/sop/template")
def template(
    layer: str = Query("L1"),
    title: str = Query("", max_length=200),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """草稿页换层级时用：L1 是完整模板，L2、L3 的步骤只写覆盖和新增。"""
    from datetime import date

    from fde_asset.modules.harvest.templates import sop_template

    if layer not in {"L1", "L2", "L3"}:
        raise HTTPException(status_code=422, detail="层级只能是 L1、L2、L3")
    return {
        "layer": layer,
        "content": sop_template(layer, title=title, date=date.today().isoformat()),
    }
