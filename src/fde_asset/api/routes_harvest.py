"""沉淀接口：草稿、检查、提交、评审、工作台、线索。"""

from __future__ import annotations

import base64
import json
import logging
from urllib.parse import quote
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Response
from sqlalchemy import or_, select, update

from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.core.db import (
    asset_feedback,
    asset_leads,
    asset_reviews,
    assets,
    harvest_candidates,
)
from fde_asset.modules.asset.indexer import (
    finish_index,
    index_repository,
)
from fde_asset.modules.asset.visibility import can_review, visibility_clause
from fde_asset.modules.asset.manifest import KIND_RULES
from fde_asset.modules.harvest import service
from fde_asset.modules.leads import rules as leads_rules
from fde_asset.modules.notify import service as notify_service
from fde_asset.platform.blobs import BlobError
from fde_asset.platform import settings_store
from fde_asset.platform.identity import Principal

logger = logging.getLogger(__name__)
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


def may_review(principal: Principal, candidate: Any) -> bool:
    """按归属范围判评审权限；从外部技能库安装的，还要公司资产评审员（脚本来源不可控）。"""
    get = candidate.get if isinstance(candidate, dict) else lambda key: getattr(candidate, key)
    if not can_review(
        principal,
        get("scope"),
        department_code=get("department_code") or "",
        engagement_slug=get("engagement_slug") or "",
        customer_code=get("customer_code") or "",
    ) and not (get("origin") == "install" and principal.is_asset_reviewer):
        return False
    if get("origin") == "install":
        return principal.is_admin or principal.is_asset_reviewer
    return True


def _reviewers(context: ServiceContext, candidate: dict[str, Any]) -> list[dict[str, str]]:
    """这份草稿该由谁评。开发模式能从目录算出具体人；正式环境名单由 fde-server 提供，返回空列表。"""
    reviewers: list[dict[str, str]] = []
    for user_id in context.directory.users():
        try:
            other = context.directory.resolve(user_id)
        except Exception:  # noqa: BLE001 - 目录里有坏数据不该影响主流程
            continue
        if other.user_id == candidate["created_by"]:
            continue  # 自己不评自己
        if may_review(other, candidate):
            reviewers.append(
                {"user_id": other.user_id, "display_name": other.display_name or other.user_id}
            )
    return reviewers


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
                # 标识可以不传，服务端会生成
                name=payload.get("name") or "",
                title=payload["title"],
                scope=payload.get("scope", "engagement"),
                department_code=payload.get("department_code", ""),
                engagement_slug=payload.get("engagement_slug", ""),
                customer_code=payload.get("customer_code", ""),
                origin=payload.get("origin", "manual"),
                source=payload.get("source", {}),
                files=payload.get("files", {}),
                lead_id=payload.get("lead_id") or "",
                visibility=payload.get("visibility") or [],
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


@router.post("/harvest-candidates/from-skill-package")
def create_from_skill_package(
    background: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """整目录导入技能：`zip_base64`（zip 包）或 `files`（{相对路径: base64}，浏览器选文件夹上传）。

    默认个人专用（scope=user）；也可以直接选项目、部门、全公司。
    """
    from fde_asset.modules.asset import skill_package

    try:
        if payload.get("zip_base64"):
            content = base64.b64decode(str(payload["zip_base64"]), validate=True)
            if len(content) > skill_package.MAX_TOTAL_BYTES * 2:
                raise HTTPException(status_code=413, detail="zip 包太大")
            package = skill_package.from_zip(content)
        elif isinstance(payload.get("files"), dict):
            package = skill_package.from_files(payload["files"])
        else:
            raise HTTPException(status_code=422, detail="要传 zip_base64 或 files")
        source = {"origin": "upload", "note": str(payload.get("filename") or "")[:200]}
        if payload.get("source_url"):
            # 网络不通时从外部技能库手动下载再导入：记下来源，以后能对比更新
            source = {
                "origin": "manual-import",
                "url": str(payload["source_url"])[:500],
                "path": str(payload.get("source_path") or "")[:300],
                "commit": str(payload.get("source_commit") or "")[:64],
            }
        candidate = service.draft_from_skill_package(
            context.engine,
            principal,
            package,
            title=str(payload.get("title") or ""),
            scope=str(payload.get("scope") or "user"),
            department_code=str(payload.get("department_code") or ""),
            engagement_slug=str(payload.get("engagement_slug") or ""),
            customer_code=str(payload.get("customer_code") or ""),
            origin="install" if payload.get("source_url") else "upload",
            source=source,
        )
    except (skill_package.SkillPackageError, service.HarvestError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _checked_upload(context, candidate, background)


def _checked_upload(
    context: ServiceContext, candidate: dict[str, Any], background: BackgroundTasks
) -> dict[str, Any]:
    """上传完马上跑规则扫描、依赖检查和试跑排队；带脚本的再在后台让模型通读一遍（几十秒，不拖慢上传）。"""
    from fde_asset.modules.asset import skill_checks, skill_package

    candidate_id = candidate["candidate_id"]
    service.run_checks(context.engine, candidate_id, _scan_context(context))
    candidate = service.get_candidate(context.engine, candidate_id)
    files = skill_checks.skill_files(candidate["files"])
    if candidate["kind"] == "Skill" and any(
        skill_package.is_script(path, text) for path, text in files.items()
    ):
        background.add_task(_model_review_in_background, context.engine, candidate_id, files)
    return candidate


def _model_llm(engine):
    from fde_asset.platform.llm.client import build_client

    llm = build_client(str(settings_store.get(engine, "rerank_model") or ""))
    config = getattr(llm, "config", None)
    if config is not None:
        config.timeout = max(config.timeout, 90)
    return llm


def _model_review_in_background(engine, candidate_id: str, files: dict[str, str]) -> None:
    from fde_asset.modules.asset import skill_checks

    try:
        skill_checks.model_review(engine, candidate_id, files, _model_llm(engine))
    except Exception:  # noqa: BLE001 - 后台通读失败不影响草稿，评审人可在页面上重新通读
        logger.exception("skill model review failed for %s", candidate_id)


@router.put("/harvest-candidates/{candidate_id}/skill-files")
def put_skill_file(
    candidate_id: str,
    background: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """在网页上改技能草稿里的任意文件：`path` + `content`（新建或覆盖）；`content` 为 null 表示删除。

    asset.yaml、附件走各自的入口；主文件 SKILL.md 不能删。改完重跑扫描和依赖检查，改了脚本再在后台让模型通读。
    """
    from fde_asset.modules.asset import skill_checks, skill_package

    candidate = get_candidate(candidate_id, context, principal)
    if candidate["kind"] != "Skill":
        raise HTTPException(status_code=422, detail="只有技能草稿能这样改文件")
    if candidate["status"] != "draft":
        raise HTTPException(status_code=409, detail="只有草稿可以修改")
    if candidate["created_by"] != principal.user_id:
        raise HTTPException(status_code=403, detail="只有草稿作者能改文件")
    path = skill_package._clean_path(str(payload.get("path") or ""))
    if (
        not path
        or path == "asset.yaml"
        or path == "attachments"
        or path.startswith("attachments/")
        or any(part.lower() == ".git" for part in path.split("/"))
    ):
        raise HTTPException(
            status_code=422,
            detail="文件路径不对：要用技能目录里的相对路径，不能改 asset.yaml 和附件",
        )
    content = payload.get("content")
    files = dict(candidate["files"])
    main = KIND_RULES["Skill"].main_file
    if content is None:
        if path == main:
            raise HTTPException(status_code=422, detail=f"{main} 是技能的主文件，不能删")
        if path not in files:
            raise HTTPException(status_code=404, detail="没有这个文件")
        files.pop(path)
    else:
        if not isinstance(content, str):
            raise HTTPException(status_code=422, detail="content 要是文字")
        if len(content.encode("utf-8")) > skill_package.MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="单个文件最大 1MB")
        files[path] = content
    package = skill_checks.skill_files(files)
    if len(package) > skill_package.MAX_FILES:
        raise HTTPException(status_code=413, detail=f"技能最多 {skill_package.MAX_FILES} 个文件")
    if sum(len(text.encode("utf-8")) for text in package.values()) > skill_package.MAX_TOTAL_BYTES:
        raise HTTPException(status_code=413, detail="技能文件合计最大 5MB")
    try:
        service.update_candidate(context.engine, candidate_id, files)
    except service.HarvestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    service.run_checks(context.engine, candidate_id, _scan_context(context))
    changed_script = skill_package.is_script(path, content or candidate["files"].get(path, ""))
    if changed_script and any(skill_package.is_script(p, t) for p, t in package.items()):
        background.add_task(_model_review_in_background, context.engine, candidate_id, package)
    return service.get_candidate(context.engine, candidate_id)


@router.post("/harvest-candidates/{candidate_id}/skill-model-review")
def skill_model_review(
    candidate_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """让模型通读一遍技能里的文字，找可疑说法。只给评审人参考，不自动拦截。"""
    from fde_asset.modules.asset import skill_checks

    candidate = get_candidate(candidate_id, context, principal)
    if candidate["kind"] != "Skill":
        raise HTTPException(status_code=422, detail="只有技能有模型通读")
    return skill_checks.model_review(
        context.engine, candidate_id, candidate["files"], _model_llm(context.engine)
    )


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
            customer_code=payload.get("customer_code", ""),
            legacy_note=payload.get("legacy_note", ""),
            store=context.blob_store,
        )
    except service.HarvestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _owner_refs(principal: Principal) -> list[str]:
    """「我负责的」口径：负责人是我本人，或者是我所在的部门。和体检、待办与信号一致。"""
    refs = [f"user:{principal.user_id}"]
    if principal.department_code:
        refs.append(f"department:{principal.department_code}")
    return refs


def _owns_revised_asset(context: ServiceContext, principal: Principal, candidate_id: str) -> bool:
    """这份草稿是不是「有人反馈我负责的资产已过时」时替我起的修订草稿。

    这种草稿建在反馈人名下，但它是给资产负责人改的：负责人打不开，「待办与信号」里
    那个草稿链接就是死的。
    """
    with context.engine.connect() as conn:
        row = conn.execute(
            select(assets.c.owner_ref)
            .select_from(
                asset_feedback.join(assets, assets.c.asset_id == asset_feedback.c.asset_id)
            )
            .where(asset_feedback.c.candidate_id == candidate_id)
        ).first()
    return row is not None and row.owner_ref in _owner_refs(principal)


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
    # 没提交之前草稿是私有的；提交之后，该作用域的评审人（如项目负责人）才能打开来评
    may_review = candidate["status"] != "draft" and can_review(
        principal,
        candidate["scope"],
        department_code=candidate["department_code"],
        engagement_slug=candidate["engagement_slug"],
        customer_code=candidate["customer_code"],
    )
    if candidate["created_by"] != principal.user_id and not (
        principal.is_admin
        or principal.is_asset_reviewer
        or may_review
        or _owns_revised_asset(context, principal, candidate_id)
    ):
        raise HTTPException(status_code=403, detail="草稿只有本人可见；提交后评审人才能打开")
    if candidate["created_by"] == principal.user_id and candidate["status"] == "merged":
        # 入库的结果本人已经看到了，平台工作台里那条不用再留着
        context.platform_inbox.resolve(
            candidate_id=candidate_id, event="result", usernames=[principal.user_id]
        )
    if candidate["kind"] == "Skill" and isinstance(candidate["checks"].get("skill"), dict):
        from fde_asset.modules.asset import skill_checks

        # 后台结果按内容摘要保存；读取当前文件对应的结果，旧任务不能覆盖新草稿的检查状态。
        candidate["checks"]["skill"]["model"] = skill_checks.model_for(
            context.engine, candidate["files"]
        )
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
        # 先合并文件再写元数据：两者都传了 asset.yaml 时，以结构化的 meta 为准
        if "files" in payload:
            service.merge_files(context.engine, candidate_id, payload["files"])
        if "meta" in payload:
            service.update_meta(context.engine, candidate_id, payload["meta"])
        if "visibility" in payload:
            service.set_visibility(context.engine, candidate_id, payload["visibility"])
    except service.HarvestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    service.run_checks(context.engine, candidate_id, _scan_context(context))
    return service.get_candidate(context.engine, candidate_id)


#: 不同作用域由谁评审，和 visibility.can_review 的规则一一对应
REVIEWER_RULE = {
    "company": "公司资产评审员（或平台管理员）",
    "department": "本部门的资产评审员或部门主管",
    "engagement": "本项目负责人",
    "customer": "资产评审员或该客户项目的负责人",
    "user": "个人专用：不带脚本的免评审；带脚本的由本部门资产评审员或部门主管评",
}
#: 从外部技能库安装的技能，不管归属哪一级，都要公司资产评审员评
INSTALL_REVIEWER_RULE = "公司资产评审员（外部技能库安装的技能）"


@router.get("/harvest-candidates/{candidate_id}/review")
def candidate_review(
    candidate_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """草稿的评审流程：当前进度、该谁评、历次记录与意见。

    作者要看得到被打回的理由，否则不知道该改什么。
    """
    candidate = get_candidate(candidate_id, context, principal)
    with context.engine.connect() as conn:
        rows = conn.execute(
            select(asset_reviews)
            .where(asset_reviews.c.candidate_id == candidate_id)
            .order_by(asset_reviews.c.submitted_at.desc())
        ).fetchall()

    history = [
        {
            "review_id": r.review_id,
            "status": r.status,
            "scope": r.scope,
            "repo": r.repo,
            "branch": r.branch,
            "summary": r.summary,
            "submitted_by": r.submitted_by,
            "submitted_at": r.submitted_at.isoformat(),
            "decided_by": r.decided_by,
            "decided_at": r.decided_at.isoformat() if r.decided_at else "",
            "note": r.note,
        }
        for r in rows
    ]
    current = next((item for item in history if item["status"] == "open"), None)

    reviewers = _reviewers(context, candidate)

    if candidate["status"] == "submitted":
        stage = "waiting"
    elif any(item["status"] == "merged" for item in history):
        stage = "approved"
    elif history and history[0]["status"] == "rejected":
        stage = "rejected"
    else:
        stage = "draft"

    return {
        "candidate_id": candidate_id,
        "stage": stage,
        "scope": candidate["scope"],
        "reviewer_rule": INSTALL_REVIEWER_RULE
        if candidate.get("origin") == "install"
        else REVIEWER_RULE.get(candidate["scope"], ""),
        "reviewers": reviewers,
        "current": current,
        "history": history,
        # 自己提交的不能自己评，哪怕本人正好有这个作用域的评审权（比如项目负责人）
        "can_review_myself": candidate["created_by"] != principal.user_id
        and can_review(
            principal,
            candidate["scope"],
            department_code=candidate["department_code"],
            engagement_slug=candidate["engagement_slug"],
            customer_code=candidate["customer_code"],
        ),
    }


@router.post("/harvest-candidates/{candidate_id}/dependencies")
def add_candidate_dependencies(
    candidate_id: str,
    payload: dict[str, Any] = Body(default={}),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """技能草稿一键补依赖：不给 packages 就按脚本里用了却没声明的 import 推断。"""
    candidate = get_candidate(candidate_id, context, principal)
    packages = payload.get("packages")
    if packages is not None and (
        not isinstance(packages, list) or not all(isinstance(p, str) for p in packages)
    ):
        raise HTTPException(status_code=422, detail="packages 要是包名列表")
    try:
        candidate, added = service.add_dependencies(context.engine, candidate, packages)
    except service.HarvestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if added:
        service.run_checks(context.engine, candidate_id, _scan_context(context))
        candidate = service.get_candidate(context.engine, candidate_id)
    return {"candidate": candidate, "added": added}


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
    customer_code = payload.get("customer_code", candidate["customer_code"])
    if scope == "customer":
        if not customer_code:
            raise HTTPException(status_code=422, detail="客户级资产必须指定客户代号")
        if not principal.is_admin and customer_code not in principal.customer_codes:
            raise HTTPException(status_code=403, detail=f"你没有参与客户 {customer_code} 的项目")
    target = context.repo_for(
        scope,
        department_code=department_code,
        engagement_slug=engagement_slug,
        customer_code=customer_code,
    )
    # 先落定作用域，再提交：评审记录要按最终作用域判定评审权限
    with context.engine.begin() as conn:
        conn.execute(
            update(harvest_candidates)
            .where(harvest_candidates.c.candidate_id == candidate_id)
            .values(
                scope=scope,
                department_code=department_code,
                engagement_slug=engagement_slug,
                customer_code=customer_code if scope == "customer" else "",
            )
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
            file_store=context.file_store,
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
                    customer_code=candidate["customer_code"],
                )
            )
        raise HTTPException(status_code=409, detail=str(exc)) from None
    submitted = service.get_candidate(context.engine, candidate_id)
    if service.personal_auto_approvable(submitted):
        # 个人专用、不带脚本、自己写的技能免人工评审（2026-10-25）：提交即入库，只对本人可见
        decided = service.decide(
            context.engine,
            principal,
            context.repo_port,
            str(submitted["review_id"]),
            approve=True,
            target_repo=target,
            note="个人专用、不含脚本的技能，按规则免人工评审",
        )
        index_repository(
            context.engine,
            context.repo_port,
            target,
            text_limit=context.settings.index_text_limit,
            file_store=context.file_store,
        )
        finish_index(context.engine, owner_of=context.target_owner)
        return {**result, "auto_approved": True, "status": decided.get("status")}
    reviewer_ids = [item["user_id"] for item in _reviewers(context, submitted)]
    review_ref = str((submitted.get("review_id") or ""))
    # 平台工作台：给评审人开一条待办；提交人之前那条"被打回"随着重新提交收掉
    context.platform_inbox.resolve(
        candidate_id=candidate_id, event="result", usernames=[principal.user_id]
    )
    context.platform_inbox.open(
        reviewer_ids,
        candidate_id=candidate_id,
        event=f"review:{review_ref}",
        title=f"资产评审：{submitted['title']}",
        body=f"{principal.display_name or principal.user_id} 提交了一份草稿，等你评审",
        engagement_slug=submitted.get("engagement_slug") or "",
    )
    notify_service.notify_review(
        context.engine,
        reviewer_ids,
        candidate_id=candidate_id,
        title=f"有草稿等你评审：{submitted['title']}",
        body=(
            f"{principal.display_name or principal.user_id} 提交了一份"
            f"{KIND_RULES[submitted['kind']].label}草稿"
        ),
        reason="你有这个作用域的评审权",
    )
    return result


@router.delete("/harvest-candidates/{candidate_id}")
def delete_candidate(
    candidate_id: str,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """删掉自己的一份草稿；如果它是从线索起草的，那条线索回到工作台。"""
    get_candidate(candidate_id, context, principal)
    try:
        return service.delete_draft(
            context.engine,
            principal,
            candidate_id,
            context.blob_store,
            # 针对我负责的资产起的修订草稿，我觉得不用改也可以直接删
            as_owner=_owns_revised_asset(context, principal, candidate_id),
        )
    except service.HarvestError as exc:
        status = 403 if "自己的" in str(exc) else 409
        raise HTTPException(status_code=status, detail=str(exc)) from None


@router.get("/reviews")
def list_reviews(
    status: str = "open",
    mine: bool = False,
    decided_by_me: bool = False,
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """评审队列与评审记录。

    - `status=open`：待评审；`mine=true` 只返回我有权决定的，用来做「待我评审」
    - `status=decided`：已经评完的（通过和打回都算），最近评的排前面；
      `decided_by_me=true` 只看我评过的。已评审的记录只给相关的人看：
      有这个作用域评审权的、提交人、评审人
    """
    decided = status == "decided"
    with context.engine.connect() as conn:
        statement = select(asset_reviews)
        if decided:
            statement = statement.where(
                asset_reviews.c.status.in_(["merged", "rejected"])
            ).order_by(asset_reviews.c.decided_at.desc())
            if decided_by_me:
                statement = statement.where(asset_reviews.c.decided_by == principal.user_id)
        else:
            statement = statement.where(asset_reviews.c.status == status).order_by(
                asset_reviews.c.submitted_at
            )
        rows = conn.execute(statement.limit(200)).fetchall()
        candidates = {
            row.candidate_id: row
            for row in conn.execute(
                select(harvest_candidates).where(
                    harvest_candidates.c.candidate_id.in_([r.candidate_id for r in rows] or [""])
                )
            )
        }

    items = []
    for row in rows:
        candidate = candidates.get(row.candidate_id)
        data = dict(row._mapping)
        data["submitted_at"] = row.submitted_at.isoformat()
        data["decided_at"] = row.decided_at.isoformat() if row.decided_at else None
        data["title"] = candidate.title if candidate else ""
        data["kind"] = candidate.kind if candidate else ""
        data["name"] = candidate.name if candidate else ""
        # 作用域内的真实归属要从候选上取，否则项目级评审永远算不出有权限
        data["can_decide"] = row.submitted_by != principal.user_id and can_review(
            principal,
            row.scope,
            department_code=candidate.department_code if candidate else "",
            engagement_slug=candidate.engagement_slug if candidate else "",
            customer_code=candidate.customer_code if candidate else "",
        )
        if mine and not data["can_decide"]:
            continue
        if decided:
            related = (
                data["can_decide"]
                or row.submitted_by == principal.user_id
                or row.decided_by == principal.user_id
            )
            if not related:
                continue
            data["decided_by_name"] = context.display_name(row.decided_by) if row.decided_by else ""
            data["asset_id"] = (
                _landed_asset_id(context, principal, candidate) if row.status == "merged" else ""
            )
        data["submitted_by_name"] = context.display_name(row.submitted_by)
        items.append(data)
    return {"items": items}


def _landed_asset_id(context: ServiceContext, principal: Principal, candidate: Any) -> str:
    """评审通过的草稿入库后是哪份资产；看不到或已经不在了就返回空，页面只给草稿链接。"""
    if candidate is None:
        return ""
    # 资产编号是由草稿编号算出来的（修订稿就是被修订的那份），不用再按层级和名字去猜
    conditions = [
        assets.c.asset_id == service.landed_asset_id(candidate),
        assets.c.deleted_at.is_(None),
        visibility_clause(principal),
    ]
    with context.engine.connect() as conn:
        row = conn.execute(select(assets.c.asset_id).where(*conditions)).first()
        if row is None:
            # 仓库合并之前入库的资产，编号是按层级和名字算的，和草稿编号对不上
            row = conn.execute(
                select(assets.c.asset_id).where(
                    assets.c.kind == candidate.kind,
                    assets.c.name == candidate.name,
                    assets.c.scope == candidate.scope,
                    assets.c.deleted_at.is_(None),
                    visibility_clause(principal),
                )
            ).first()
    return row.asset_id if row else ""


def _apply_executables(context, target, review, candidate, requested) -> None:
    """评审通过的技能：哪些脚本给执行权限。没传就按默认（没有高风险、试跑通过）；只能在可勾选的里面挑。"""
    current = service.get_candidate(context.engine, candidate.candidate_id)
    skill = (current.get("checks") or {}).get("skill") or {}
    tickable = set(skill.get("tickable") or [])
    if requested is None:
        chosen = list(skill.get("default_executables") or [])
    else:
        if not isinstance(requested, list) or not all(isinstance(p, str) for p in requested):
            raise HTTPException(status_code=422, detail="executables 要是脚本路径列表")
        refused = sorted(set(requested) - tickable)
        if refused:
            raise HTTPException(
                status_code=422,
                detail="这些脚本不能给执行权限（试跑没过或镜像里没有解释器）："
                + "、".join(refused),
            )
        chosen = list(requested)
    if chosen or (current.get("checks") or {}).get("skill"):
        service.set_executables(
            context.repo_port, target, review.branch, current, chosen, engine=context.engine
        )


def _confirm_deps(context, candidate, confirmed) -> None:
    """共享技能要装运行镜像里没有的依赖：评审人要明确确认（个人技能免确认，只限数量）。"""
    if candidate.scope == "user":
        return
    current = service.get_candidate(context.engine, candidate.candidate_id)
    deps = ((current.get("checks") or {}).get("skill") or {}).get("deps") or {}
    if deps.get("new") and confirmed is not True:
        raise HTTPException(
            status_code=422,
            detail="这个技能要预装运行环境里没有的依赖："
            + "、".join(deps["new"])
            + "。确认来源可信后勾选「确认预装这些依赖」再通过",
        )


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
    if not may_review(principal, candidate):
        raise HTTPException(
            status_code=403,
            detail="外部技能库安装的技能要由公司资产评审员评"
            if candidate.origin == "install"
            else "没有该作用域的评审权限",
        )
    if review.submitted_by == principal.user_id:
        # 评审的意义就是换一个人看。项目负责人、管理员提交的也一样，要由别的评审人来评
        raise HTTPException(status_code=403, detail="不能评审自己提交的资产，请由其他评审人处理")
    target = context.repo_for(
        review.scope,
        department_code=candidate.department_code,
        engagement_slug=candidate.engagement_slug,
        customer_code=candidate.customer_code,
    )
    approve = bool(payload.get("approve", True))
    if approve and candidate.kind == "Skill" and review.status == "open":
        _confirm_deps(context, candidate, payload.get("deps_confirmed"))
        _apply_executables(context, target, review, candidate, payload.get("executables"))
    result = service.decide(
        context.engine,
        principal,
        context.repo_port,
        review_id,
        approve=approve,
        target_repo=target,
        note=payload.get("note", ""),
    )
    if not result.get("idempotent"):
        approved = result.get("status") == "merged"
        note = str(payload.get("note", "")).strip()
        # 平台工作台：评完了，所有评审人名下的那条待办收掉；结果给提交人开一条
        context.platform_inbox.resolve(candidate_id=candidate.candidate_id, event="review")
        context.platform_inbox.open(
            [candidate.created_by],
            candidate_id=candidate.candidate_id,
            event=f"result:{review_id}",
            title=("资产已入库：" if approved else "资产被打回：") + candidate.title,
            body=note
            or ("评审通过，已合并进资产仓库" if approved else "评审未通过，请修改后重新提交"),
            engagement_slug=candidate.engagement_slug or "",
        )
        notify_service.notify_review(
            context.engine,
            [candidate.created_by],
            candidate_id=candidate.candidate_id,
            title=("已入库：" if approved else "被打回：") + candidate.title,
            body=(note or ("评审通过，资产已合并进仓库" if approved else "请按意见修改后重新提交")),
            reason=f"{principal.display_name or principal.user_id} 评审了你提交的草稿",
        )
    if result.get("status") == "merged":
        report = index_repository(
            context.engine,
            context.repo_port,
            target,
            text_limit=context.settings.index_text_limit,
            file_store=context.file_store,
        )
        # 只索引了这一个仓库，收尾（关系、版本、通知订阅的人有新资产）要另外做
        finish_index(context.engine, owner_of=context.target_owner)
        result["index"] = {
            "indexed": report.indexed,
            "invalid": report.invalid,
            "head": report.head,
        }
        if payload.get("mandatory") is True:
            result["mandatory"] = _mark_mandatory(context, principal, review.scope, candidate)
        extra = json.loads(getattr(candidate, "visibility_json", "") or "[]")
        if extra:
            result["visibility"] = _apply_extra_visibility(context, principal, candidate, extra)
    return result


def _apply_extra_visibility(
    context: ServiceContext, principal: Principal, candidate, extra: list[str]
) -> dict[str, Any]:
    """起草时多选的可见范围：评审人有权放的直接加上，没权放的以起草人的名义转成申请。"""
    from fde_asset.api.routes_visibility import announce
    from fde_asset.modules.asset import sharing
    from fde_asset.modules.asset import visibility_requests

    asset_id = service.landed_asset_id(candidate)
    with context.engine.connect() as conn:
        row = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
    if row is None:
        return {"applied": [], "requested": [], "error": "入库后的资产没找到"}
    from fde_asset.modules.asset.manifest import visibility_entry

    current = sharing.normalize(
        json.loads(row.visibility_json or "[]")
        or [
            visibility_entry(
                row.scope,
                department_code=row.department_code or "",
                customer_code=row.customer_code or "",
                engagement_slug=row.engagement_slug or "",
            )
        ]
    )
    wanted_all = sharing.normalize([*current, *extra])
    added = [pair for pair in sharing.audiences(wanted_all) if sharing._entry(*pair) not in current]
    allowed = [pair for pair in added if sharing._may_add(principal, *pair)]
    pending = [sharing._entry(*pair) for pair in added if pair not in allowed]
    applied: list[str] = []
    error = ""
    if allowed:
        wanted = [entry for entry in wanted_all if entry not in pending]
        terms_path = context.settings.root / "compliance.json"
        terms = json.loads(terms_path.read_text(encoding="utf-8")) if terms_path.exists() else {}
        try:
            sharing.commit_visibility(
                context.engine,
                context.repo_port,
                principal,
                row,
                current=current,
                wanted=wanted,
                added=allowed,
                note="评审通过时按起草时选的可见范围加上",
                customer_names=tuple(terms.get("customer_names", [])),
                sensitive_terms=tuple(terms.get("sensitive_terms", [])),
                text_limit=context.settings.index_text_limit,
                owner_of=context.target_owner,
                file_store=context.file_store,
            )
            applied = [sharing._entry(*pair) for pair in allowed]
        except sharing.SharingError as exc:
            # 比如放到全公司时扫出了客户名：入库照常，这几项不加，告诉评审人
            error = str(exc)
    requested = (
        visibility_requests.create(
            context.engine,
            asset_id=asset_id,
            entries=pending,
            requested_by=candidate.created_by,
            note="起草时选的可见范围，评审人没有这一层的权限",
        )
        if pending
        else []
    )
    if requested:
        announce(context, requested, title=row.title, by=candidate.created_by)
    return {"applied": applied, "requested": [item["entry"] for item in requested], "error": error}


def _mark_mandatory(
    context: ServiceContext, principal: Principal, scope: str, candidate
) -> dict[str, Any]:
    """评审通过时顺手设成本作用域的必选资产。设不上不影响入库，原因带回给页面。"""
    from fde_asset.modules.asset import mandatory

    code = {
        "department": candidate.department_code,
        "customer": candidate.customer_code,
        "engagement": candidate.engagement_slug,
    }.get(scope, "")
    entry = "company" if scope == "company" else f"{scope}:{code}"
    try:
        saved, _ = mandatory.add(
            context.engine,
            principal,
            service.landed_asset_id(candidate),
            entry,
            context={
                "department_code": candidate.department_code,
                "customer_code": candidate.customer_code,
            },
        )
    except mandatory.MandatoryError as exc:
        return {"entry": entry, "ok": False, "error": str(exc)}
    return {"entry": saved.key, "ok": True}


@router.get("/workbench")
def workbench(
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """资产工作台：自由添加入口 + 线索 + 我的草稿 + 我提交的 + 我负责的。"""
    with context.engine.connect() as conn:
        # 我的草稿 = 我自己建的 + 别人反馈「已过时」时替我负责的资产起的修订草稿
        revision_ids = [
            row.candidate_id
            for row in conn.execute(
                select(asset_feedback.c.candidate_id)
                .select_from(
                    asset_feedback.join(assets, assets.c.asset_id == asset_feedback.c.asset_id)
                )
                .where(
                    asset_feedback.c.candidate_id != "",
                    assets.c.owner_ref.in_(_owner_refs(principal)),
                )
            )
        ]
        drafts = conn.execute(
            select(harvest_candidates)
            .where(
                or_(
                    harvest_candidates.c.created_by == principal.user_id,
                    harvest_candidates.c.candidate_id.in_(revision_ids or [""]),
                ),
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
