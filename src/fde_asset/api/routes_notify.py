"""订阅与站内通知接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.modules.notify import service
from fde_asset.platform.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["notify"])


@router.get("/subscriptions")
def list_subscriptions(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return {
        "items": service.list_subscriptions(context.engine, principal),
        "filter_types": list(service.FILTER_TYPES),
    }


@router.post("/subscriptions")
def subscribe(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    try:
        return service.subscribe(
            context.engine,
            principal,
            payload.get("filter_type", ""),
            str(payload.get("filter_value", "")),
        )
    except service.NotifyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.delete("/subscriptions/{subscription_id}")
def unsubscribe(
    subscription_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not service.unsubscribe(context.engine, principal, subscription_id):
        raise HTTPException(status_code=404, detail="订阅不存在")
    return {"removed": True}


@router.get("/notifications")
def notifications(
    unread_only: bool = True,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return {"items": service.inbox(context.engine, principal, unread_only=unread_only)}


@router.post("/notifications/{notification_id}/read")
def read_notification(
    notification_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not service.mark_read(context.engine, principal, notification_id):
        raise HTTPException(status_code=404, detail="通知不存在")
    return {"read": True}


@router.get("/assets/{asset_id}/changes")
def asset_changes(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """这份资产最近一次更新改了什么，章节级。"""
    from fde_asset.modules.asset import catalog

    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权访问")
    return service.diff_summary(context.engine, asset_id)
