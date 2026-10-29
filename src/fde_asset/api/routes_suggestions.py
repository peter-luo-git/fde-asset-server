"""沉淀候选的页面接口：查看、人在消息上「记为可沉淀」、起草后标记、忽略（2026-10-28）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.modules.harvest import suggestions
from fde_asset.platform.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["harvest-suggestions"])


def _fail(exc: suggestions.SuggestionError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/harvest-suggestions/counts")
def suggestion_counts(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """红色数字：我能看到的待处理候选数，按项目分（项目「资产」菜单、工作台卡片用）。"""
    return suggestions.counts(context.engine, principal)


@router.get("/harvest-suggestions")
def list_suggestions(
    engagement_slug: str = Query(default=""),
    scope: str = Query(default=""),
    scope_id: str = Query(default=""),
    status: str = Query(default="open"),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """给了 engagement_slug：项目「资产 → 待沉淀」（status=all 是全部）；给了 scope、scope_id：这个频道或私聊里的；
    都不给：我能看到的、还没处理的（工作台用）。"""
    if engagement_slug:
        if not principal.is_admin and engagement_slug not in principal.engagement_slugs:
            raise HTTPException(status_code=404, detail="项目不存在或无权访问")
        items = suggestions.list_for_project(
            context.engine, principal, engagement_slug, "" if status == "all" else status
        )
    elif scope and scope_id:
        items = suggestions.list_for_scope(context.engine, principal, scope, scope_id, status)
    else:
        items = suggestions.list_mine(context.engine, principal)
    return {"items": items}


@router.post("/harvest-suggestions")
def mark_message(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """人在消息上「记为可沉淀」：频道里要是项目成员，私聊只记给自己。"""
    engagement_slug = str(payload.get("engagement_slug") or "")
    try:
        principal = suggestions.current_principal(context.directory, principal)
        if not suggestions.may_write_project(principal, engagement_slug):
            raise suggestions.SuggestionError("只有项目可写成员能记录沉淀候选", 403)
        source = suggestions.checked_source(context.directory, principal, payload, message=True)
        return suggestions.record(
            context.engine,
            engagement_slug=source["engagement_slug"],
            scope=source["scope"],
            scope_id=source["scope_id"],
            owner_user=principal.user_id,
            source="message",
            created_by=principal.user_id,
            kind=str(payload.get("kind") or ""),
            title=str(payload.get("title") or ""),
            summary=str(payload.get("summary") or ""),
            conversation_id=source["conversation_id"],
            message_id=source["message_id"],
        )
    except suggestions.SuggestionError as exc:
        raise _fail(exc) from None


@router.post("/harvest-suggestions/{suggestion_id}/decide")
def decide(
    suggestion_id: str,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """处理：drafted 起草了（带 candidate_id）、linked 已有资产（带 asset_id）、duplicate 重复（带 merged_into）、
    ignored 不沉淀（可带 note）、hold 待定、open 恢复。"""
    try:
        principal = suggestions.current_principal(context.directory, principal)
        status = str(payload.get("status") or "")
        if status == "drafted" and payload.get("candidate_id"):
            from fde_asset.api.routes_harvest import get_candidate

            get_candidate(str(payload["candidate_id"]), context, principal)
        if status == "linked" and payload.get("asset_id"):
            from fde_asset.modules.recommend import service

            if not service.visible_to(context.engine, principal, [str(payload["asset_id"])]):
                raise HTTPException(status_code=404, detail="资产不存在或不可见")
        return suggestions.decide(
            context.engine,
            principal,
            suggestion_id,
            status,
            str(payload.get("candidate_id") or ""),
            note=str(payload.get("note") or ""),
            asset_id=str(payload.get("asset_id") or ""),
            merged_into=str(payload.get("merged_into") or ""),
        )
    except suggestions.SuggestionError as exc:
        raise _fail(exc) from None
