"""可见范围申请的审批接口，以及选可见范围时用的名单（部门、客户、项目、人）。"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.modules.asset import catalog, sharing
from fde_asset.modules.asset import visibility_requests as requests_module
from fde_asset.modules.notify import service as notify_service
from fde_asset.platform.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["visibility"])


def _terms(context: ServiceContext) -> dict[str, Any]:
    compliance = context.settings.root / "compliance.json"
    return json.loads(compliance.read_text(encoding="utf-8")) if compliance.exists() else {}


def _deciders(context: ServiceContext, entry: str, *, exclude: str = "") -> list[str]:
    """能批这一项的人。开发模式从名单里算；正式环境名单来自 fde-server。"""
    found: list[str] = []
    try:
        users = context.directory.users()
    except Exception:  # noqa: BLE001 - 名单取不到就不发通知，不影响申请本身
        return found
    for user_id in users:
        if user_id == exclude:
            continue
        try:
            other = context.directory.resolve(user_id)
        except Exception:  # noqa: BLE001 - 名单里有坏数据不影响主流程
            continue
        if requests_module.may_decide(other, entry):
            found.append(user_id)
    return found


def announce(
    context: ServiceContext, requested: list[dict[str, Any]], *, title: str, by: str
) -> None:
    """新提的申请通知能批的人。"""
    for item in requested:
        if item.get("status") != "open":
            continue
        notify_service.notify_users(
            context.engine,
            _deciders(context, item["entry"], exclude=item["requested_by"]),
            kind="visibility",
            asset_id=item["asset_id"],
            title=f"可见范围申请：{title}",
            body=f"{by} 申请把它放到「{item['label']}」",
            reason="你有这一层的评审权",
        )


def _settings_args(context: ServiceContext) -> dict[str, Any]:
    terms = _terms(context)
    return {
        "customer_names": tuple(terms.get("customer_names", [])),
        "sensitive_terms": tuple(terms.get("sensitive_terms", [])),
        "text_limit": context.settings.index_text_limit,
        "owner_of": context.target_owner,
        "file_store": context.file_store,
    }


@router.get("/visibility-requests")
def list_requests(
    view: Literal["todo", "mine", "decided"] = Query(
        "todo", description="todo 等我批的 / mine 我提的 / decided 我批过的"
    ),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return {"items": requests_module.listing(context.engine, principal, view=view)}


@router.get("/assets/{asset_id}/visibility-requests")
def asset_requests(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    items = requests_module.open_requests(context.engine, asset_id)
    for item in items:
        item["can_decide"] = (
            requests_module.may_decide(principal, item["entry"])
            and item["requested_by"] != principal.user_id
        )
        item["can_withdraw"] = item["requested_by"] == principal.user_id
    return {"items": items}


@router.post("/visibility-requests/{request_id}/decide")
def decide_request(
    request_id: str,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    request = requests_module.get(context.engine, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="申请不存在")
    note = str(payload.get("note", "")).strip()
    approve = bool(payload.get("approve", True))
    try:
        if approve:
            result = sharing.approve_request(
                context.engine,
                context.repo_port,
                principal,
                request_id,
                note=note,
                **_settings_args(context),
            )
        else:
            if request.status != "open":
                return {"request_id": request_id, "status": request.status, "idempotent": True}
            if not requests_module.may_decide(principal, request.entry):
                raise sharing.SharingForbidden("要由那一层的评审人审批")
            if not note:
                raise sharing.SharingError("驳回要写清楚原因")
            requests_module.mark(
                context.engine, request_id, status="rejected", by=principal.user_id, note=note
            )
            result = {"request_id": request_id, "status": "rejected"}
    except sharing.SharingForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except sharing.SharingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if not result.get("idempotent"):
        label = requests_module.describe(request.entry)
        notify_service.notify_users(
            context.engine,
            [request.requested_by],
            kind="visibility",
            asset_id=request.asset_id,
            title=("可见范围申请已批准：" if approve else "可见范围申请被驳回：") + label,
            body=note or ("已加上这一项" if approve else ""),
            reason=f"{principal.display_name or principal.user_id} 处理了你的申请",
        )
    return result


@router.post("/visibility-requests/{request_id}/withdraw")
def withdraw_request(
    request_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    request = requests_module.get(context.engine, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="申请不存在")
    if request.requested_by != principal.user_id:
        raise HTTPException(status_code=403, detail="只能撤回自己提的申请")
    if request.status == "open":
        requests_module.mark(context.engine, request_id, status="withdrawn", by=principal.user_id)
    return {"request_id": request_id, "status": "withdrawn"}


@router.get("/audience-options")
def audience_options(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """选可见范围时的下拉名单：部门、客户、项目、人。名单取不到时各项为空，页面退回手填。"""
    try:
        engagements = context.directory.engagements()
        users = {}
        for user_id in context.directory.users():
            try:
                users[user_id] = context.directory.resolve(user_id)
            except Exception:  # noqa: BLE001 - 名单里有坏数据就跳过这个人
                continue
        names = getattr(context.directory, "departments", lambda: {})()
    except Exception:  # noqa: BLE001 - 名单服务不可用：页面退回手填代号
        return {"departments": [], "customers": [], "engagements": [], "users": [], "partial": True}
    departments: dict[str, str] = dict(names)
    customers: dict[str, list[str]] = {}
    for slug, raw in engagements.items():
        if raw.get("department_code"):
            departments.setdefault(raw["department_code"], raw["department_code"])
        if raw.get("customer_code"):
            customers.setdefault(raw["customer_code"], []).append(raw.get("title") or slug)
    for person in users.values():
        if person.department_code:
            departments.setdefault(person.department_code, person.department_code)
        for membership in person.memberships:
            if membership.customer_code:
                customers.setdefault(membership.customer_code, [])
            if membership.engagement_slug and membership.engagement_slug not in engagements:
                engagements = {**engagements, membership.engagement_slug: {}}
    return {
        "departments": [
            {"code": code, "label": name if name == code else f"{name}（{code}）"}
            for code, name in sorted(departments.items())
        ],
        "customers": [
            {
                "code": code,
                "label": code + (f"（{'、'.join(sorted(set(titles))[:3])}）" if titles else ""),
            }
            for code, titles in sorted(customers.items())
        ],
        "engagements": [
            {"code": slug, "label": f"{raw.get('title') or slug}（{slug}）"}
            for slug, raw in sorted(engagements.items())
        ],
        "users": [
            {"code": user_id, "label": f"{person.display_name or user_id}（{user_id}）"}
            for user_id, person in sorted(users.items())
        ],
        "partial": False,
    }
