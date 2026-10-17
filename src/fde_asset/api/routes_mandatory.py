"""必选资产接口：资产详情页上设置、取消，治理页上按范围看清单。规则见 `modules/asset/mandatory.py`。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.modules.asset import catalog
from fde_asset.modules.asset import mandatory
from fde_asset.platform.identity import Principal, PrincipalNotFound

router = APIRouter(prefix="/api/v1", tags=["mandatory"])


def _scope_context(context: ServiceContext, principal: Principal, entry: str) -> dict[str, str]:
    """项目、个人这两级判断可见时，要知道它们所属的部门和客户。"""
    scope, code = mandatory.parse(entry)
    if scope == "engagement":
        membership = next((m for m in principal.memberships if m.engagement_slug == code), None)
        project = context.directory.engagements().get(code, {})
        return {
            "department_code": str(project.get("department_code", ""))
            or (membership.department_code if membership else ""),
            "customer_code": membership.customer_code if membership else "",
        }
    if scope == "user":
        try:
            person = context.directory.resolve(code)
        except PrincipalNotFound:
            raise mandatory.MandatoryError("没有这个账号") from None
        return {
            "department_codes": ",".join(sorted(person.department_codes)),
            "customer_codes": ",".join(sorted(person.customer_codes)),
            "engagement_slugs": ",".join(sorted(person.engagement_slugs)),
        }
    return {}


def _fail(exc: mandatory.MandatoryError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _view(principal: Principal, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        row["can_remove"] = mandatory.may_manage(principal, row["scope"], row["code"])
    return rows


@router.get("/assets/{asset_id}/mandatory")
def asset_mandatory(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """这份资产在哪些范围是必选，以及我能给哪些范围设。"""
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    rows = [row for row in mandatory.listing(context.engine) if row["asset_id"] == asset_id]
    return {
        "entries": _view(principal, rows),
        "manageable": mandatory.manageable_scopes(principal),
        "is_admin": principal.is_admin,
    }


@router.post("/assets/{asset_id}/mandatory")
def add_mandatory(
    asset_id: str,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """设为必选。`entry`：company，或 department:/customer:/engagement:/user: 加代号。"""
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    entry = str(payload.get("entry", ""))
    try:
        saved, created = mandatory.add(
            context.engine,
            principal,
            asset_id,
            entry,
            context=_scope_context(context, principal, entry),
        )
    except mandatory.MandatoryError as exc:
        raise _fail(exc) from None
    return {"entry": saved.key, "created": created}


@router.post("/assets/{asset_id}/mandatory/remove")
def remove_mandatory(
    asset_id: str,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    try:
        removed = mandatory.remove(
            context.engine, principal, asset_id, str(payload.get("entry", ""))
        )
    except mandatory.MandatoryError as exc:
        raise _fail(exc) from None
    return {"removed": removed}


@router.get("/mandatory")
def list_mandatory(
    scope: str = Query(
        "", description="只看某一种范围：company/department/customer/engagement/user"
    ),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """必选资产清单。管理员看全部；其他人看自己负责的范围。"""
    if principal.is_admin:
        rows = mandatory.listing(context.engine)
    else:
        mine = [mandatory.parse(key) for key in mandatory.manageable_scopes(principal)]
        rows = mandatory.listing(context.engine, scopes=mine)
    if scope:
        rows = [row for row in rows if row["scope"] == scope]
    return {
        "items": _view(principal, rows),
        "manageable": mandatory.manageable_scopes(principal),
        "is_admin": principal.is_admin,
    }
