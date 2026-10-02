"""推荐接口：为项目和 Agent 找资产、推给负责人、接收或忽略。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.modules.recommend import service
from fde_asset.platform import settings_store
from fde_asset.platform.identity import Principal
from fde_asset.platform.llm.client import build_client
from fde_asset.platform.llm.reranker import build_reranker

router = APIRouter(prefix="/api/v1", tags=["recommend"])


def _target(context: ServiceContext, payload: dict[str, Any]) -> service.Target:
    try:
        target = service.target_from_directory(
            context.directory, payload.get("target_type", ""), payload.get("target_id", "")
        )
    except service.RecommendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    # 调用方可以覆盖上下文（试验台那种手填场景）
    for field in ("title", "description", "industry", "department_code", "stage", "role"):
        if payload.get(field):
            setattr(target, field, payload[field])
    return target


def _may_manage(principal: Principal, target: service.Target) -> bool:
    """本人负责、同部门的部门主管，或管理员，才能推送与关联。"""
    if principal.is_admin:
        return True
    if target.owner and target.owner == principal.user_id:
        return True
    if target.target_type == "engagement" and principal.owns_engagement(target.target_id):
        return True
    return principal.is_department_head and principal.department_code == target.department_code


@router.get("/recommend/targets")
def list_targets(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """我能为哪些项目和 Agent 找资产：我负责的 + （部门主管）本部门全部。"""
    mine: list[dict[str, Any]] = []
    department: list[dict[str, Any]] = []
    for target_type, source in (
        ("engagement", context.directory.engagements()),
        ("agent", context.directory.agents()),
    ):
        for target_id, raw in source.items():
            item = {
                "target_type": target_type,
                "target_id": target_id,
                "title": raw.get("title", target_id),
                "owner": raw.get("owner", ""),
                "department_code": raw.get("department_code", ""),
                "industry": raw.get("industry", ""),
                "stage": raw.get("stage", ""),
                "role": raw.get("role", ""),
                "description": raw.get("description", ""),
            }
            owned = raw.get("owner") == principal.user_id or (
                target_type == "engagement" and principal.owns_engagement(target_id)
            )
            if owned or principal.is_admin:
                mine.append(item)
            if (principal.is_department_head or principal.is_admin) and raw.get(
                "department_code"
            ) == principal.department_code:
                department.append(item)
    return {"mine": mine, "department": department}


@router.post("/recommend/compute")
def compute(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """算推荐，不落库。页面上先给人看，人点了再保存或推送。"""
    target = _target(context, payload)
    # 快速模式只跑关键词粗排；精确模式再交给模型按内容理解精排并写理由
    accurate = payload.get("mode", "accurate") != "fast"
    enabled = bool(settings_store.get(context.engine, "rerank_enabled"))
    llm = None
    reranker = None
    if accurate and enabled:
        llm = build_client(str(settings_store.get(context.engine, "rerank_model") or ""))
        reranker = build_reranker()
    items, mode = service.compute(
        context.engine,
        principal,
        target,
        limit=int(payload.get("limit", service.DEFAULT_LIMIT)),
        llm=llm,
        reranker=reranker,
    )
    return {
        "mode": mode,
        "target": {
            "target_type": target.target_type,
            "target_id": target.target_id,
            "title": target.title,
            "owner": target.owner,
            "department_code": target.department_code,
        },
        "items": items,
    }


@router.post("/recommend/send")
def send(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """部门管理员推送给负责人；负责人自己保存时 status 传 suggested。"""
    target = _target(context, payload)
    if not _may_manage(principal, target):
        raise HTTPException(status_code=403, detail="只有目标负责人、本部门主管或管理员能推送")
    status = payload.get("status", "sent")
    if status not in ("sent", "suggested"):
        raise HTTPException(status_code=422, detail="status 只能是 sent 或 suggested")
    items = payload.get("items") or []
    if not items:
        raise HTTPException(status_code=422, detail="没有选中任何资产")

    # 推给别人之前先确认对方看得见：推一个他打不开的资产，只会变成一条死待办
    skipped_invisible: list[str] = []
    if target.owner and target.owner != principal.user_id:
        try:
            owner = context.directory.resolve(target.owner)
        except Exception:  # noqa: BLE001 - 目录查不到就不做这层过滤
            owner = None
        if owner is not None:
            allowed = service.visible_to(
                context.engine, owner, [item["asset_id"] for item in items]
            )
            skipped_invisible = [
                item["asset_id"] for item in items if item["asset_id"] not in allowed
            ]
            items = [item for item in items if item["asset_id"] in allowed]
    if not items:
        raise HTTPException(
            status_code=422,
            detail="选中的资产对方都看不到，换成公司级资产，或先把它们的作用域放开",
        )

    source = "dept_admin" if target.owner != principal.user_id else "self"
    result = service.save(context.engine, principal, target, items, source=source, status=status)
    result["skipped_invisible"] = skipped_invisible
    return result


@router.get("/recommend/list")
def list_recommendations(
    target_type: str,
    target_id: str,
    status: str = "",
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return {
        "items": service.list_for_target(
            context.engine, principal, target_type, target_id, status=status
        )
    }


@router.get("/recommend/inbox")
def inbox(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return {"items": service.inbox(context.engine, principal)}


@router.post("/recommend/{recommendation_id}/decide")
def decide(
    recommendation_id: str,
    payload: dict[str, Any] = Body(default={}),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    try:
        return service.decide(
            context.engine,
            principal,
            recommendation_id,
            accept=bool(payload.get("accept", True)),
            note=payload.get("note", ""),
        )
    except service.RecommendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/recommend/link")
def link(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """本人直接关联，不走推荐流程。"""
    target = _target(context, payload)
    if not _may_manage(principal, target):
        raise HTTPException(status_code=403, detail="只有目标负责人或管理员能关联资产")
    return service.link(
        context.engine, principal, target.target_type, target.target_id, payload["asset_id"]
    )


@router.get("/recommend/linked")
def linked(
    target_type: str,
    target_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return {"items": service.linked_assets(context.engine, principal, target_type, target_id)}
