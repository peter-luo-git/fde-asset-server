"""资产目录、详情、引用、SOP、快照、使用事件接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.core.db import asset_index_findings, assets
from fde_asset.modules.asset import catalog
from fde_asset.modules.app import health as app_health_module
from fde_asset.modules.asset import (
    ask,
    checkup,
    feedback,
    matching,
    relations,
    snapshot,
    sop,
    usage,
)
from fde_asset.modules.asset.indexer import index_all
from fde_asset.modules.asset.manifest import KIND_RULES
from fde_asset.modules.harvest import service as harvest_service
from fde_asset.platform.identity import Principal
from fde_asset.platform import settings_store
from fde_asset.platform.llm.reranker import build_reranker
from fde_asset.platform.refs.wiki import parse_refs

router = APIRouter(prefix="/api/v1", tags=["assets"])


@router.get("/assets")
def list_assets(
    kind: str | None = None,
    scope: str | None = None,
    industry: str | None = None,
    owner_department: str | None = None,
    owner: str | None = None,
    owner_kind: str | None = None,
    # personal=我个人负责；department=我部门负责；any=两者都算
    mine: str | None = None,
    lifecycle: str | None = None,
    quality: str | None = None,
    nature: str | None = None,
    q: str | None = None,
    sort: str = "updated",
    include_deprecated: bool = False,
    limit: int = Query(50, le=200),
    offset: int = 0,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if mine == "personal":
        owner = f"user:{principal.user_id}"
    elif mine == "department":
        owner = f"department:{principal.department_code}"
    elif mine == "any":
        # 「我负责的」= 我本人 + 我所在部门，和工作台口径一致
        owner_kind = None
        owner = None
    query = catalog.CatalogQuery(
        kind=kind,
        scope=scope,
        industry=industry,
        owner_department=owner_department,
        owner=owner,
        owner_kind=owner_kind,
        owner_any=(
            [f"user:{principal.user_id}", f"department:{principal.department_code}"]
            if mine == "any"
            else None
        ),
        lifecycle=lifecycle,
        quality=quality,
        nature=nature,
        q=q,
        sort=sort,
        include_deprecated=include_deprecated,
        limit=limit,
        offset=offset,
    )
    return catalog.search(context.engine, principal, query)


@router.get("/assets/invalid")
def list_invalid(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not (principal.is_admin or principal.is_asset_reviewer):
        raise HTTPException(status_code=403, detail="只有管理员或资产评审员可以查看无效资产")
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(asset_index_findings).order_by(asset_index_findings.c.id.desc())
        ).fetchall()
    return {
        "items": [dict(row._mapping) | {"detected_at": row.detected_at.isoformat()} for row in rows]
    }


@router.get("/assets/ask")
def ask_assets(
    q: str,
    kind: str | None = None,
    scope: str | None = None,
    limit: int = Query(ask.DEFAULT_LIMIT, ge=1, le=50),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """按问题检索：一句话进来，按语义相关度出资产；没配重排模型时退回关键词。"""
    if not q.strip():
        raise HTTPException(status_code=422, detail="问题不能为空")
    return ask.ask(
        context.engine,
        principal,
        q,
        reranker=build_reranker(),
        kind=kind,
        scope=scope,
        limit=limit,
        min_score=int(settings_store.get(context.engine, "search_min_relevance")) / 100,
    )


@router.get("/assets/resolve")
def resolve(
    ref: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    refs = parse_refs(ref if ref.startswith("[[") else f"[[{ref}]]")
    if not refs:
        return {"status": "asset_not_visible", "ref": ref}
    first = refs[0]
    return catalog.resolve_ref(
        context.engine, principal, first.short_kind, first.name, first.version
    )


@router.post("/assets/references")
def add_references(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """解析一段文本里的 [[引用]] 并记录（频道消息、工作项描述、交付摘要共用）。"""
    text = payload.get("text", "")
    source_type = payload.get("source_type", "message")
    source_id = str(payload.get("source_id", ""))
    engagement_slug = payload.get("engagement_slug", "")
    recorded, skipped = [], []
    for ref in parse_refs(text):
        card = catalog.resolve_ref(context.engine, principal, ref.short_kind, ref.name, ref.version)
        if card["status"] != "ok":
            skipped.append(ref.text)
            continue
        created = usage.record_reference(
            context.engine,
            asset_id=card["asset_id"],
            source_type=source_type,
            source_id=f"{source_id}:{ref.name}",
            engagement_slug=engagement_slug,
            created_by=principal.user_id,
            asset_version=card.get("version", ""),
        )
        recorded.append({"ref": ref.text, "asset_id": card["asset_id"], "new": created})
    return {"recorded": recorded, "skipped": skipped}


@router.get("/assets/{asset_id}")
def get_asset(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    data = catalog.get_asset(context.engine, principal, asset_id)
    if data is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    return data


@router.get("/assets/{asset_id}/passport")
def get_passport(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    data = catalog.passport(context.engine, principal, asset_id)
    if data is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    return data


@router.get("/assets/{asset_id}/sop-steps")
def get_sop_steps(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    return sop.merged_steps(context.engine, asset_id)


@router.get("/assets/{asset_id}/sop-steps/{step_number}")
def get_sop_step(
    asset_id: str,
    step_number: int,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权查看")
    return sop.step_summary(context.engine, asset_id, step_number)


@router.post("/assets/usages")
def post_usages(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    items = [usage.UsageInput(**item) for item in payload.get("items", [])]
    return usage.record_usages(context.engine, items)


@router.post("/snapshots")
def build_snapshot(
    payload: dict[str, Any] = Body(default={}),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    result = snapshot.build_snapshot(
        context.engine,
        context.repo_port,
        snapshot_root=context.settings.snapshot_dir,
        department_code=payload.get("department_code", ""),
        engagement_slug=payload.get("engagement_slug", ""),
        index_limit=context.settings.knowledge_index_limit,
        industries=tuple(payload.get("industries", [])),
    )
    return {
        "sha": result.sha,
        "path": str(result.path),
        "reused": result.reused,
        "counts": result.counts,
        "index_bytes": result.index_bytes,
        "index_truncated": result.index_truncated,
        "skipped": result.skipped,
    }


@router.post("/assets/{asset_id}/feedback")
def submit_feedback(
    asset_id: str,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """用过之后表个态；选「已过时」会自动起一份修订草稿交给负责人。"""
    verdict = payload.get("verdict", "")
    note = payload.get("note", "")
    try:
        result = feedback.submit(
            context.engine,
            principal,
            asset_id=asset_id,
            verdict=verdict,
            note=note,
            engagement_slug=payload.get("engagement_slug", ""),
        )
    except feedback.FeedbackError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    if verdict == "outdated" and payload.get("draft_revision", True):
        with context.engine.connect() as conn:
            row = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
        if row is not None:
            draft = feedback.revision_draft_payload(row, note, principal.user_id)
            try:
                candidate = harvest_service.create_draft(
                    context.engine,
                    principal,
                    harvest_service.CandidateInput(
                        kind=draft["kind"],
                        name=draft["name"],
                        title=draft["title"],
                        scope=draft["scope"],
                        department_code=draft["department_code"],
                        engagement_slug=draft["engagement_slug"],
                        origin="feedback",
                        source={
                            "origin": "feedback",
                            "note": f"{principal.user_id} 反馈已过时：{note}",
                        },
                    ),
                )
            except harvest_service.HarvestError:
                candidate = None
            if candidate is not None:
                feedback.attach_candidate(
                    context.engine, asset_id, principal.user_id, candidate["candidate_id"]
                )
                result["candidate_id"] = candidate["candidate_id"]
    return result


@router.get("/assets/{asset_id}/feedback")
def read_feedback(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权访问")
    return feedback.summary(context.engine, asset_id)


@router.get("/governance/signals")
def governance_signals(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """负责人待办：我负责的资产收到的负面反馈，以及被反复拒绝的推荐。"""
    return feedback.owner_signals(context.engine, principal)


@router.get("/governance/checkup")
def asset_checkup(
    owner_only: bool = True,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """资产体检：半年没更新、零复用、被反复拒绝、收到差评，四条规则。"""
    return checkup.run(context.engine, principal, owner_only=owner_only)


@router.get("/settings")
def read_settings(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return {"items": settings_store.describe(context.engine)}


@router.put("/settings")
def write_settings(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="只有平台管理员能改系统配置")
    try:
        values = settings_store.update(
            context.engine, payload.get("values", {}), updated_by=principal.user_id
        )
    except settings_store.SettingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"values": values, "items": settings_store.describe(context.engine)}


@router.get("/apps")
def list_apps(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """应用市场：能跑的东西，带演示入口与在线状态。"""
    result = catalog.search(
        context.engine, principal, catalog.CatalogQuery(kind="Application", limit=200)
    )
    health = app_health_module.health_map(context.engine)
    items = []
    for item in result["items"]:
        spec = item.get("kind_spec") or {}
        demo = spec.get("demo") or {}
        runtime = spec.get("runtime") or {}
        items.append(
            {
                **item,
                "source_type": spec.get("sourceType", ""),
                "maturity": spec.get("maturity", ""),
                "repo": spec.get("repo", ""),
                "runtime": runtime,
                "demo": demo,
                "health": health.get(item["asset_id"], {"status": "unknown"}),
            }
        )
    return {"total": len(items), "items": items}


@router.post("/apps/probe")
def probe_apps(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """手动探一遍演示地址。定时任务也调它。"""
    if not (principal.is_admin or principal.is_asset_reviewer):
        raise HTTPException(status_code=403, detail="只有管理员或资产评审员能触发探活")
    results = app_health_module.probe_all(context.engine)
    tally: dict[str, int] = {}
    for item in results:
        tally[item.status] = tally.get(item.status, 0) + 1
    return {"checked": len(results), "by_status": tally}


@router.get("/assets/{asset_id}/relations")
def asset_relations_view(
    asset_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """这份资产的上下游：引用了谁、被谁引用、同项目还沉淀了什么。"""
    if catalog.get_asset(context.engine, principal, asset_id) is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权访问")
    return relations.neighbours(context.engine, principal, asset_id)


@router.get("/assets/{asset_id}/graph")
def asset_relation_graph(
    asset_id: str,
    depth: int = Query(2, ge=1, le=relations.GRAPH_MAX_DEPTH),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """关系图谱：以这份资产为中心往外展开几层，节点全部经过可见性过滤。"""
    result = relations.graph(context.engine, principal, asset_id, depth)
    if result is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权访问")
    return result


@router.post("/admin/assets/relink")
def rebuild_relations(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not (principal.is_admin or principal.is_asset_reviewer):
        raise HTTPException(status_code=403, detail="只有管理员或资产评审员可以重建关系")
    return relations.auto_link(context.engine)


@router.get("/dev/identities")
def dev_identities(context: ServiceContext = Depends(get_context)) -> dict[str, Any]:
    """开发模式下可以切换成哪些身份，给页面上的身份切换用。

    这个接口本身不需要身份——它就是用来选身份的。正式环境（oidc）没有这回事，直接 404。
    """
    if context.settings.identity_mode != "dev":
        raise HTTPException(status_code=404, detail="只有开发模式能切换身份")
    items = []
    for user_id in context.directory.users():
        principal = context.directory.resolve(user_id)
        roles = [
            label
            for flag, label in (
                (principal.is_admin, "平台管理员"),
                (principal.is_asset_reviewer, "资产评审员"),
                (principal.is_department_head, "部门主管"),
            )
            if flag
        ]
        roles += [f"{m.engagement_slug} 负责人" for m in principal.memberships if m.role == "owner"]
        items.append(
            {
                "user_id": principal.user_id,
                "display_name": principal.display_name or principal.user_id,
                "department_code": principal.department_code,
                "roles": roles,
                "engagements": sorted(principal.engagement_slugs),
            }
        )
    return {"items": items}


@router.get("/kinds")
def list_kinds() -> dict[str, Any]:
    """七种资产类型各自的必填要求，前端据此标红必填项，避免两边各写一份口径。"""
    return {
        "items": [
            {
                "kind": rule.kind,
                "label": rule.label,
                "main_file": rule.main_file,
                "directory": rule.directory,
                "requires_summary": rule.requires_summary,
                "requires_applicability": rule.requires_applicability,
                "required_sections": list(rule.required_sections),
                "company_required_sections": list(rule.company_required_sections),
                "required_spec_fields": list(rule.required_spec_fields),
            }
            for rule in KIND_RULES.values()
        ]
    }


@router.post("/match")
def match_assets(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """按项目/工作项/Agent 的上下文推荐该带哪些资产，每条都带推荐理由。"""
    match_context = matching.MatchContext(
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        industry=payload.get("industry", ""),
        department_code=payload.get("department_code", "") or principal.department_code,
        engagement_slug=payload.get("engagement_slug", ""),
        agent_role=payload.get("agent_role", ""),
        stage=payload.get("stage", ""),
        keywords=list(payload.get("keywords", []) or []),
    )
    return matching.match(context.engine, principal, match_context)


@router.post("/admin/assets/reindex")
def reindex(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    if not (principal.is_admin or principal.is_asset_reviewer):
        raise HTTPException(status_code=403, detail="只有管理员或资产评审员可以触发重建索引")
    reports = index_all(
        context.engine,
        context.repo_port,
        context.repos(),
        text_limit=context.settings.index_text_limit,
    )
    return {
        "repos": [
            {
                "repo": r.repo,
                "head": r.head,
                "indexed": r.indexed,
                "invalid": r.invalid,
                "removed": r.removed,
            }
            for r in reports
        ]
    }
