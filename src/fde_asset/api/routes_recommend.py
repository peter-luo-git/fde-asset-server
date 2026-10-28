"""推荐接口：为项目和 Agent 找资产、推给负责人、接收或忽略。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from fde_asset.api.routes_assets import _customer_of
from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.modules.asset import rerank as rerank_module
from fde_asset.modules.recommend import service
from fde_asset.platform import settings_store
from fde_asset.platform.cache import recommendation_cache
from fde_asset.platform.identity import Principal
from fde_asset.platform.llm.client import build_client
from fde_asset.platform.llm.reranker import build_reranker

router = APIRouter(prefix="/api/v1", tags=["recommend"])

#: 按需写理由时等生成模型多久
EXPLAIN_TIMEOUT_SECONDS = 90.0


def _target(
    context: ServiceContext, payload: dict[str, Any], *, override_context: bool = True
) -> service.Target:
    try:
        target = service.target_from_directory(
            context.directory, payload.get("target_type", ""), payload.get("target_id", "")
        )
    except service.RecommendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    # 只读匹配允许试验台覆盖上下文；写操作必须用目录归属授权。
    if override_context:
        for field in ("title", "description", "industry", "department_code", "stage", "role"):
            if payload.get(field):
                setattr(target, field, payload[field])
    return target


def _owns(principal: Principal, target: service.Target) -> bool:
    """这个项目或 Agent 是不是他负责的（管理员视同负责人）。只有负责人能直接关联资产。"""
    if principal.is_admin:
        return True
    if target.owner and target.owner == principal.user_id:
        return True
    return target.target_type == "engagement" and principal.owns_engagement(target.target_id)


def _may_manage(principal: Principal, target: service.Target) -> bool:
    """能不能为它找资产并推送：负责人，或者同部门的部门主管。

    部门主管对同事负责的目标只能推荐，不能替人关联——关联要用 `_owns` 判。
    """
    if _owns(principal, target):
        return True
    if target.target_type == "agent" and not target.owner:
        return False
    return bool(target.department_code) and (
        principal.is_department_head and principal.department_code == target.department_code
    )


def _may_decide(context: ServiceContext, principal: Principal, sent_to: dict[str, str]) -> bool:
    """推送时记下的负责人，或者目标现在的负责人（项目会换负责人）。"""
    if principal.is_admin or sent_to["target_owner"] == principal.user_id:
        return True
    try:
        target = service.target_from_directory(
            context.directory, sent_to["target_type"], sent_to["target_id"]
        )
    except Exception:  # noqa: BLE001 - 目标已经不在名单里，只认推送时的负责人
        return False
    return _owns(principal, target)


@router.get("/recommend/targets")
def list_targets(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """我能为哪些项目和 Agent 找资产：我负责的 + （部门主管）本部门全部。"""
    mine: list[dict[str, Any]] = []
    department: list[dict[str, Any]] = []
    owner_names: dict[str, str] = {}
    for user_id in context.directory.users():
        try:
            owner_names[user_id] = context.directory.resolve(user_id).display_name or user_id
        except Exception:  # noqa: BLE001 - 名单里有坏数据就显示账号，不影响主流程
            owner_names[user_id] = user_id
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
            # 部门视图里自己负责的和同事负责的走不同流程，页面要分得清
            item["mine"] = owned
            item["owner_name"] = owner_names.get(raw.get("owner", ""), raw.get("owner", ""))
            if owned or principal.is_admin:
                mine.append(item)
            if (
                (principal.is_department_head or principal.is_admin)
                and raw.get("department_code")
                and raw["department_code"] == principal.department_code
            ):
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
    # 快速模式只跑关键词粗排；精确模式交给 rerank 模型按内容理解排序。
    # 理由不在这里写：生成模型写字要十几秒，改成人点了才生成（见 /recommend/explain）
    accurate = payload.get("mode", "accurate") != "fast"
    enabled = bool(settings_store.get(context.engine, "rerank_enabled"))
    llm = None
    reranker = build_reranker() if accurate and enabled else None
    limit = int(payload.get("limit", service.DEFAULT_LIMIT))
    # 缓存键带上用户：可见范围因人而异，不能把别人能看的缓存给我
    cache_key = ":".join(
        [
            principal.user_id,
            target.target_type,
            target.target_id,
            "accurate" if accurate and enabled else "fast",
            str(limit),
            target.title,
            target.description[:80],
            target.industry,
            target.stage,
        ]
    )
    cached = None if payload.get("refresh") else recommendation_cache.get(cache_key)
    if cached is not None:
        items, mode = cached
        mode = f"{mode}-cached"
    else:
        items, mode = service.compute(
            context.engine,
            principal,
            target,
            limit=limit,
            llm=llm,
            reranker=reranker,
            min_score=int(settings_store.get(context.engine, "search_min_relevance")) / 100,
        )
        recommendation_cache.set(cache_key, (items, mode))
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


@router.post("/recommend/explain")
def explain(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """按需写推荐理由：人想知道「为什么推这份」时才调生成模型，一次最多 5 份。"""
    target = _target(context, payload)
    asset_ids = [str(item) for item in payload.get("asset_ids") or []][
        : rerank_module.EXPLAIN_LIMIT
    ]
    if not asset_ids:
        raise HTTPException(status_code=422, detail="asset_ids 不能为空")
    cache_key = ":".join(
        ["explain", principal.user_id, target.target_type, target.target_id, target.title]
        + [target.description[:80], target.stage, ",".join(sorted(asset_ids))]
    )
    cached = None if payload.get("refresh") else recommendation_cache.get(cache_key)
    if cached is not None:
        return {"available": True, "reasons": cached, "cached": True}
    llm = build_client(str(settings_store.get(context.engine, "rerank_model") or ""))
    config = getattr(llm, "config", None)
    if config is not None:
        # 写理由是人点了才等的，给够时间：实测生成模型写两句话要三十秒上下，默认的 30 秒超时刚好卡在边上
        config.timeout = max(config.timeout, EXPLAIN_TIMEOUT_SECONDS)
    reasons = service.explain(context.engine, principal, target, asset_ids, llm)
    if reasons is None:
        # 没配生成模型或它这次没成功：照实说，页面上仍然有相关度和规则给的理由
        return {"available": False, "reasons": {}}
    recommendation_cache.set(cache_key, reasons)
    return {"available": True, "reasons": reasons, "cached": False}


@router.post("/recommend/send")
def send(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """部门管理员推送给负责人；负责人自己保存时 status 传 suggested。"""
    target = _target(context, payload, override_context=False)
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
    # 接收就会写关联（目标是 Agent 时等于改它的资产包），只能由目标负责人或管理员来定
    sent_to = service.recipient(context.engine, recommendation_id)
    if sent_to is not None and not _may_decide(context, principal, sent_to):
        raise HTTPException(status_code=403, detail="只有推荐对象的负责人或管理员能接收或忽略")
    try:
        result = service.decide(
            context.engine,
            principal,
            recommendation_id,
            accept=bool(payload.get("accept", True)),
            note=payload.get("note", ""),
        )
        recommendation_cache.invalidate()
        return result
    except service.RecommendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/recommend/link")
def link(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """本人直接关联，不走推荐流程。"""
    target = _target(context, payload, override_context=False)
    if not _owns(principal, target):
        raise HTTPException(status_code=403, detail="只有目标负责人或管理员能关联资产")
    asset_id = str(payload.get("asset_id", ""))
    if target.target_type == "engagement" and asset_id:
        # 项目看不到的资产，关联了 Agent 也拿不到（快照按项目的可见范围挑）：直接说清楚，不让关联
        audience = _project_audience(context, target)
        if not service.visible_to_project(context.engine, [asset_id], **audience):
            label = service.scope_label(context.engine, asset_id, _department_names(context))
            raise HTTPException(
                status_code=422,
                detail=f"这个项目看不到这份资产（可见范围：{label}），关联了 Agent 也拿不到。"
                "请先在资产详情的「调整可见范围」里加上这个项目或它所在的部门，再关联",
            )
    try:
        result = service.link(
            context.engine, principal, target.target_type, target.target_id, payload["asset_id"]
        )
    except service.RecommendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    # 关联之后推荐结果就变了（已关联的不再推），清掉这个目标的缓存
    recommendation_cache.invalidate()
    return result


@router.get("/recommend/linked")
def linked(
    target_type: str,
    target_id: str,
    include_own: bool = False,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """关联给目标的资产。`include_own=true` 且目标是项目时，再带上项目自己的资产（source=project），
    它们不用关联也会给项目的 Agent。"""
    items = service.linked_assets(context.engine, principal, target_type, target_id)
    if target_type == "engagement" and items:
        # 已经关联、但项目看不到的（比如别的部门的资产）：标出来，Agent 实际不会带上
        target = service.target_from_directory(context.directory, target_type, target_id)
        visible = service.visible_to_project(
            context.engine,
            {item["asset_id"] for item in items},
            **_project_audience(context, target),
        )
        departments = _department_names(context)
        for item in items:
            item["project_can_see"] = item["asset_id"] in visible
            if not item["project_can_see"]:
                item["scope_label"] = service.scope_label(
                    context.engine, item["asset_id"], departments
                )
    if include_own and target_type == "engagement":
        items += [
            {**item, "project_can_see": True}
            for item in service.project_own_assets(
                context.engine, principal, target_id, {item["asset_id"] for item in items}
            )
        ]
    return {"items": items}


def _project_audience(context: ServiceContext, target: service.Target) -> dict[str, str]:
    """项目的部门、客户：和生成 Agent 资产快照时用的一样。"""
    project = context.directory.engagements().get(target.target_id, {})
    return {
        "engagement_slug": target.target_id,
        "department_code": target.department_code or str(project.get("department_code", "")),
        "customer_code": str(project.get("customer_code", ""))
        or _customer_of(context, target.target_id),
    }


def _department_names(context: ServiceContext) -> dict[str, str]:
    try:
        return dict(context.directory.departments())
    except Exception:  # noqa: BLE001 - 部门名只用来写提示，拿不到就用代号
        return {}
