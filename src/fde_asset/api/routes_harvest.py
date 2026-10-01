"""沉淀接口：草稿、检查、提交、评审、工作台、线索。"""

from __future__ import annotations

import base64
import json
from urllib.parse import quote
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import select, update

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.core.db import asset_leads, asset_reviews, assets, harvest_candidates
from fde_asset.modules.asset.indexer import index_repository
from fde_asset.modules.asset.visibility import can_review, visibility_clause
from fde_asset.modules.harvest import service
from fde_asset.modules.leads import rules as leads_rules
from fde_asset.platform.blobs import BlobError
from fde_asset.platform.identity import Principal

router = APIRouter(prefix="/api/v1", tags=["harvest"])


def _scan_context(context: ServiceContext) -> service.ScanContext:
    """客户名称与敏感词：正式环境来自 fde-server，本地从目录文件读。"""
    path = context.settings.root / "compliance.json"
    if not path.exists():
        return service.ScanContext()
    data = json.loads(path.read_text(encoding="utf-8"))
    return service.ScanContext(
        customer_names=tuple(data.get("customer_names", [])),
        sensitive_terms=tuple(data.get("sensitive_terms", [])),
    )


@router.post("/harvest-candidates")
def create_candidate(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    try:
        return service.create_draft(
            context.engine,
            principal,
            service.CandidateInput(
                kind=payload["kind"],
                name=payload["name"],
                title=payload["title"],
                scope=payload.get("scope", "engagement"),
                department_code=payload.get("department_code", ""),
                engagement_slug=payload.get("engagement_slug", ""),
                origin=payload.get("origin", "manual"),
                source=payload.get("source", {}),
                files=payload.get("files", {}),
            ),
        )
    except service.HarvestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/harvest-candidates/from-issue")
def create_from_issue(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    try:
        return service.draft_from_issue(
            context.engine,
            principal,
            work_item_id=str(payload["work_item_id"]),
            title=payload["title"],
            description=payload.get("description", ""),
            severity=payload.get("severity", "S3"),
            root_cause=payload.get("root_cause", ""),
            prevention=payload.get("prevention", ""),
            resolution=payload.get("resolution", ""),
            engagement_slug=payload.get("engagement_slug", ""),
            customer_code=payload.get("customer_code", ""),
        )
    except service.HarvestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/harvest-candidates/from-upload")
def create_from_upload(
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """历史文档导入：content 为 base64。提取文本预填正文，结论仍需人工补齐。"""
    content = base64.b64decode(payload["content_base64"])
    if len(content) > context.settings.attachment_size_limit:
        raise HTTPException(status_code=413, detail="附件超过大小上限")
    try:
        return service.draft_from_upload(
            context.engine,
            principal,
            kind=payload.get("kind", "Solution"),
            title=payload["title"],
            filename=payload["filename"],
            content=content,
            scope=payload.get("scope", "company"),
            department_code=payload.get("department_code", ""),
            engagement_slug=payload.get("engagement_slug", ""),
            legacy_note=payload.get("legacy_note", ""),
            store=context.blob_store,
        )
    except service.HarvestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/harvest-candidates/{candidate_id}")
def get_candidate(
    candidate_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    try:
        candidate = service.get_candidate(context.engine, candidate_id)
    except service.HarvestError:
        raise HTTPException(status_code=404, detail="候选不存在") from None
    if candidate["created_by"] != principal.user_id and not (
        principal.is_admin or principal.is_asset_reviewer
    ):
        raise HTTPException(status_code=403, detail="草稿只有本人可见")
    return candidate


@router.patch("/harvest-candidates/{candidate_id}")
def patch_candidate(
    candidate_id: str,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    candidate = get_candidate(candidate_id, context, principal)
    if candidate["status"] != "draft":
        raise HTTPException(status_code=409, detail="只有草稿可以修改")
    try:
        if "meta" in payload:
            service.update_meta(context.engine, candidate_id, payload["meta"])
        if "files" in payload:
            service.merge_files(context.engine, candidate_id, payload["files"])
    except service.HarvestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    service.run_checks(context.engine, candidate_id, _scan_context(context))
    return service.get_candidate(context.engine, candidate_id)


@router.post("/harvest-candidates/{candidate_id}/attachments")
def add_attachment(
    candidate_id: str,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """给草稿加附件：content_base64 为原件内容，支持 PDF / Word / Excel / PPT / Markdown。"""
    get_candidate(candidate_id, context, principal)
    content = base64.b64decode(payload["content_base64"])
    try:
        result = service.attach_file(
            context.engine,
            candidate_id,
            filename=payload["filename"],
            content=content,
            store=context.blob_store,
            size_limit=context.settings.attachment_size_limit,
        )
    except BlobError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except service.HarvestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    service.run_checks(context.engine, candidate_id, _scan_context(context))
    return result


@router.get("/harvest-candidates/{candidate_id}/attachments/{filename}")
def download_attachment(
    candidate_id: str,
    filename: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> Response:
    get_candidate(candidate_id, context, principal)
    try:
        content = service.read_attachment(candidate_id, filename, context.blob_store)
    except BlobError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if content is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    # 中文文件名不能直接塞进 HTTP 头（头只认 latin-1），按 RFC 5987 编码
    quoted = quote(filename)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"
        },
    )


@router.delete("/harvest-candidates/{candidate_id}/attachments/{filename}")
def remove_attachment(
    candidate_id: str,
    filename: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    get_candidate(candidate_id, context, principal)
    try:
        candidate = service.detach_file(
            context.engine, candidate_id, filename=filename, store=context.blob_store
        )
    except BlobError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except service.HarvestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    service.run_checks(context.engine, candidate_id, _scan_context(context))
    return candidate


@router.post("/harvest-candidates/{candidate_id}/checks")
def post_checks(
    candidate_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    get_candidate(candidate_id, context, principal)
    return service.run_checks(context.engine, candidate_id, _scan_context(context))


@router.post("/harvest-candidates/{candidate_id}/submit")
def submit_candidate(
    candidate_id: str,
    payload: dict[str, Any] = Body(default={}),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    candidate = get_candidate(candidate_id, context, principal)
    scope = payload.get("scope", candidate["scope"])
    department_code = payload.get(
        "department_code", candidate["department_code"] or principal.department_code
    )
    engagement_slug = payload.get("engagement_slug", candidate["engagement_slug"])
    target = context.repo_for(
        scope, department_code=department_code, engagement_slug=engagement_slug
    )
    # 先落定作用域，再提交：评审记录要按最终作用域判定评审权限
    with context.engine.begin() as conn:
        conn.execute(
            update(harvest_candidates)
            .where(harvest_candidates.c.candidate_id == candidate_id)
            .values(scope=scope, department_code=department_code, engagement_slug=engagement_slug)
        )
    try:
        result = service.submit(
            context.engine,
            principal,
            context.repo_port,
            candidate_id,
            target_repo=target,
            scan_context=_scan_context(context),
            medium_risk_confirmed=bool(payload.get("medium_risk_confirmed")),
            blob_store=context.blob_store,
        )
    except service.HarvestError as exc:
        # 提交失败时把作用域回滚为原值，避免草稿状态与实际不符
        with context.engine.begin() as conn:
            conn.execute(
                update(harvest_candidates)
                .where(harvest_candidates.c.candidate_id == candidate_id)
                .values(
                    scope=candidate["scope"],
                    department_code=candidate["department_code"],
                    engagement_slug=candidate["engagement_slug"],
                )
            )
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return result


@router.get("/reviews")
def list_reviews(
    status: str = "open",
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(asset_reviews)
            .where(asset_reviews.c.status == status)
            .order_by(asset_reviews.c.submitted_at)
        ).fetchall()
    items = []
    for row in rows:
        data = dict(row._mapping)
        data["submitted_at"] = row.submitted_at.isoformat()
        data["decided_at"] = row.decided_at.isoformat() if row.decided_at else None
        data["can_decide"] = can_review(
            principal, row.scope, department_code=principal.department_code
        )
        items.append(data)
    return {"items": items}


@router.post("/reviews/{review_id}/decide")
def decide_review(
    review_id: str,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    with context.engine.connect() as conn:
        review = conn.execute(
            select(asset_reviews).where(asset_reviews.c.review_id == review_id)
        ).first()
        candidate = conn.execute(
            select(harvest_candidates).where(
                harvest_candidates.c.candidate_id == (review.candidate_id if review else "")
            )
        ).first()
    if review is None or candidate is None:
        raise HTTPException(status_code=404, detail="评审不存在")
    if not can_review(
        principal,
        review.scope,
        department_code=candidate.department_code,
        engagement_slug=candidate.engagement_slug,
    ):
        raise HTTPException(status_code=403, detail="没有该作用域的评审权限")
    target = context.repo_for(
        review.scope,
        department_code=candidate.department_code,
        engagement_slug=candidate.engagement_slug,
    )
    result = service.decide(
        context.engine,
        principal,
        context.repo_port,
        review_id,
        approve=bool(payload.get("approve", True)),
        target_repo=target,
        note=payload.get("note", ""),
    )
    if result.get("status") == "merged":
        report = index_repository(
            context.engine, context.repo_port, target, text_limit=context.settings.index_text_limit
        )
        result["index"] = {
            "indexed": report.indexed,
            "invalid": report.invalid,
            "head": report.head,
        }
    return result


@router.get("/workbench")
def workbench(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """资产工作台：自由添加入口 + 线索 + 我的草稿 + 我提交的 + 我负责的。"""
    with context.engine.connect() as conn:
        drafts = conn.execute(
            select(harvest_candidates)
            .where(
                harvest_candidates.c.created_by == principal.user_id,
                harvest_candidates.c.status == "draft",
            )
            .order_by(harvest_candidates.c.updated_at.desc())
        ).fetchall()
        submitted = conn.execute(
            select(harvest_candidates)
            .where(
                harvest_candidates.c.created_by == principal.user_id,
                harvest_candidates.c.status.in_(["submitted", "merged", "rejected"]),
            )
            .order_by(harvest_candidates.c.updated_at.desc())
            .limit(20)
        ).fetchall()
        owned = conn.execute(
            select(assets)
            .where(
                visibility_clause(principal),
                assets.c.owner_value.in_([principal.user_id, principal.department_code]),
            )
            .limit(50)
        ).fetchall()
        leads = conn.execute(
            select(asset_leads)
            .where(
                asset_leads.c.owner_user == principal.user_id,
                asset_leads.c.status == "open",
            )
            .order_by(asset_leads.c.score.desc())
            .limit(20)
        ).fetchall()
    return {
        "add_options": [
            {"key": "blank", "label": "空白模板", "hint": "选类型后按模板写"},
            {
                "key": "upload",
                "label": "上传历史文档",
                "hint": "PDF / Word / Excel / PPT，提取文本后补结论",
            },
            {"key": "from_activity", "label": "从工作项或问题单起草", "hint": "平台自动带出上下文"},
            {"key": "paste", "label": "粘贴一段文字", "hint": "从聊天记录或笔记里粘"},
        ],
        "leads": [dict(row._mapping) | {"created_at": row.created_at.isoformat()} for row in leads],
        "drafts": [
            {
                "candidate_id": r.candidate_id,
                "kind": r.kind,
                "title": r.title,
                "origin": r.origin,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in drafts
        ],
        "submitted": [
            {
                "candidate_id": r.candidate_id,
                "kind": r.kind,
                "title": r.title,
                "status": r.status,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in submitted
        ],
        "owned": [
            {
                "asset_id": r.asset_id,
                "kind": r.kind,
                "title": r.title,
                "scope": r.scope,
                "lifecycle": r.lifecycle,
                "owner_ref": r.owner_ref,
                "owner_kind": r.owner_kind,
            }
            for r in owned
        ],
    }


@router.post("/workbench/leads/refresh")
def refresh_leads(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return leads_rules.refresh(context.engine, context.activity)


@router.post("/workbench/leads/{lead_id}/ignore")
def ignore_lead(
    lead_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    with context.engine.begin() as conn:
        row = conn.execute(select(asset_leads).where(asset_leads.c.lead_id == lead_id)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="线索不存在")
        if row.owner_user != principal.user_id:
            raise HTTPException(status_code=403, detail="线索只有本人可以处理")
        conn.execute(
            update(asset_leads).where(asset_leads.c.lead_id == lead_id).values(status="ignored")
        )
    return {"lead_id": lead_id, "status": "ignored"}
