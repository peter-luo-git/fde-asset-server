"""沉淀链路：草稿 → 检查 → 提交 → 评审 → 合并 → 重新索引。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from fde_asset.core.db import (
    asset_leads,
    asset_reviews,
    assets,
    harvest_candidates,
    record_event,
)
from fde_asset.modules.asset.manifest import (
    KIND_RULES,
    NAME_PATTERN,
    SHORT_BY_KIND,
    parse_manifest,
    validate_asset,
)
from fde_asset.modules.harvest.templates import render_template
from fde_asset.platform.blobs import BlobStore, safe_name
from fde_asset.platform.extract import extract_text
from fde_asset.platform.identity import Principal
from fde_asset.platform.repo.ports import RepoRef
from fde_asset.platform.scan.rules import blocking, scan_files


class HarvestError(ValueError):
    pass


@dataclass
class ScanContext:
    customer_names: tuple[str, ...] = ()
    sensitive_terms: tuple[str, ...] = ()


@dataclass
class CandidateInput:
    kind: str
    name: str
    title: str
    scope: str = "engagement"
    department_code: str = ""
    engagement_slug: str = ""
    customer_code: str = ""
    origin: str = "manual"
    source: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    #: 从哪条沉淀线索起草的；填了就把那条线索标成已起草，不再出现在工作台
    lead_id: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_name(engine: Engine, kind: str) -> str:
    """没填标识时替用户起一个：类型前缀-日期-四位随机串，例如 `case-20261005-a3f2`。

    标识是资产的地址（Git 目录名、`[[kind/name]]` 引用、接口参数），只能是 ASCII；
    但中文标题转不出像样的 ASCII，逼着人现想一个英文名是纯负担，所以默认自动生成。
    """
    prefix = SHORT_BY_KIND.get(kind, kind.lower())
    stamp = _now().strftime("%Y%m%d")
    with engine.connect() as conn:
        for _ in range(20):
            name = f"{prefix}-{stamp}-{uuid.uuid4().hex[:4]}"
            taken = (
                conn.execute(
                    select(harvest_candidates.c.candidate_id).where(
                        harvest_candidates.c.name == name
                    )
                ).first()
                or conn.execute(
                    select(assets.c.asset_id).where(assets.c.kind == kind, assets.c.name == name)
                ).first()
            )
            if not taken:
                return name
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:12]}"


def create_draft(engine: Engine, principal: Principal, data: CandidateInput) -> dict[str, Any]:
    if data.kind not in KIND_RULES:
        raise HarvestError(f"不支持的 kind：{data.kind}")
    name = (data.name or "").strip()
    if not name:
        name = generate_name(engine, data.kind)
    elif not NAME_PATTERN.match(name) or len(name) > 64:
        raise HarvestError("标识只能用小写字母、数字和连字符；不填会自动生成")
    data = replace(data, name=name)
    if data.lead_id:
        with engine.connect() as conn:
            lead = conn.execute(
                select(asset_leads.c.owner_user).where(asset_leads.c.lead_id == data.lead_id)
            ).first()
        # 线索是给某个人的待办，别人不能替他"处理掉"
        if lead is None or lead.owner_user != principal.user_id:
            raise HarvestError("线索不存在，或者不是给你的")
        data = replace(data, origin="lead", source={**data.source, "leadId": data.lead_id})
    if data.scope == "engagement" and not data.engagement_slug:
        raise HarvestError("项目级候选必须指定 engagement_slug")
    if data.scope == "department" and not data.department_code:
        raise HarvestError("部门级候选必须指定 department_code")
    if data.scope == "customer":
        if not data.customer_code:
            raise HarvestError("客户级候选必须指定 customer_code")
        # 只有参与这个客户项目的人才写得了它的资产；否则任何人都能往别的客户仓库里塞东西
        if not principal.is_admin and data.customer_code not in principal.customer_codes:
            raise HarvestError(f"你没有参与客户 {data.customer_code} 的项目，不能为它沉淀资产")

    owner = (
        f"department:{principal.department_code}"
        if principal.department_code
        else f"user:{principal.user_id}"
    )
    files = data.files or render_template(
        data.kind,
        name=data.name,
        title=data.title,
        owner=owner,
        date=_now().date().isoformat(),
    )
    candidate_id = uuid.uuid4().hex[:12]
    with engine.begin() as conn:
        conn.execute(
            harvest_candidates.insert().values(
                candidate_id=candidate_id,
                kind=data.kind,
                scope=data.scope,
                department_code=data.department_code,
                engagement_slug=data.engagement_slug,
                customer_code=data.customer_code if data.scope == "customer" else "",
                name=data.name,
                title=data.title,
                status="draft",
                origin=data.origin,
                source_json=json.dumps(data.source, ensure_ascii=False),
                files_json=json.dumps(files, ensure_ascii=False),
                checks_json="{}",
                created_by=principal.user_id,
            )
        )
        if data.lead_id:
            # 只处理这一条：同一个工作项可能还有别的线索，各自说的是不同的事
            conn.execute(
                update(asset_leads)
                .where(asset_leads.c.lead_id == data.lead_id)
                .values(status="drafted")
            )
        record_event(
            conn,
            "candidate.created",
            {
                "candidate_id": candidate_id,
                "kind": data.kind,
                "origin": data.origin,
                "scope": data.scope,
                "created_by": principal.user_id,
            },
        )
    return get_candidate(engine, candidate_id)


def draft_from_issue(
    engine: Engine,
    principal: Principal,
    *,
    work_item_id: str,
    title: str,
    description: str,
    severity: str,
    root_cause: str,
    prevention: str,
    resolution: str = "",
    engagement_slug: str = "",
    customer_code: str = "",
) -> dict[str, Any]:
    """问题单关闭为已修复时生成 Case 草稿。S1/S2 必须填根因。"""
    if severity in {"S1", "S2"} and not root_cause.strip():
        raise HarvestError("S1/S2 问题单必须填写根因")
    slug = _slugify(title, fallback="")
    name = f"issue-{work_item_id}" + (f"-{slug}" if slug else "")
    body = "\n".join(
        [
            f"# {title}",
            "",
            "## 结论",
            (root_cause or "待补充").strip(),
            "",
            "## 现象",
            description.strip() or "待补充",
            "",
            "## 影响",
            f"严重程度 {severity}",
            "",
            "## 根因",
            root_cause.strip() or "待补充",
            "",
            "## 解决方法",
            resolution.strip() or "待补充",
            "",
            "## 如何避免",
            prevention.strip() or "待补充",
            "",
            "## 判断要点",
            "<为什么看着对其实不对>",
            "",
        ]
    )
    owner = (
        f"department:{principal.department_code}"
        if principal.department_code
        else f"user:{principal.user_id}"
    )
    manifest = "\n".join(
        [
            "apiVersion: fde.asset/v1",
            "kind: Case",
            "metadata:",
            f"  name: {name}",
            f"  title: {title}",
            f"  summary: {root_cause.strip()[:110] or '待补充'}",
            "  tags: []",
            "spec:",
            f"  owner: {owner}",
            "  lifecycle: experimental",
            "  industry: []",
            "  applicability:",
            '    suitable: ""',
            '    notSuitable: ""',
            "  version: 0.1.0",
            "  caseType: fault",
            f"  severity: {severity}",
            "  source:",
            f'    workItemId: "{work_item_id}"',
        ]
    )
    if engagement_slug:
        manifest += f"\n    engagementSlug: {engagement_slug}"
    if customer_code:
        manifest += f"\n    customerCode: {customer_code}"
    return create_draft(
        engine,
        principal,
        CandidateInput(
            kind="Case",
            name=name,
            title=title,
            scope="engagement",
            engagement_slug=engagement_slug,
            origin="issue_close",
            source={"workItemId": work_item_id, "engagementSlug": engagement_slug},
            files={"README.md": body, "asset.yaml": manifest},
        ),
    )


def draft_from_upload(
    engine: Engine,
    principal: Principal,
    *,
    kind: str,
    title: str,
    filename: str,
    content: bytes,
    scope: str = "company",
    department_code: str = "",
    engagement_slug: str = "",
    customer_code: str = "",
    legacy_note: str = "",
    store: BlobStore | None = None,
) -> dict[str, Any]:
    """历史文档导入：提取文本预填正文，结论与适用边界留给人补，原件存进 BlobStore。"""
    extraction = extract_text(filename, content)
    # 中文标题转不出 ASCII，以前一律落成 legacy-import，多传几份就重名了
    name = _slugify(title, fallback="") or generate_name(engine, kind)
    owner = (
        f"department:{principal.department_code}"
        if principal.department_code
        else f"user:{principal.user_id}"
    )
    rule = KIND_RULES[kind]
    excerpt = (
        extraction.text[:4000] if extraction.status == "ok" else "（未能提取文本，请人工补充要点）"
    )
    sections = "\n\n".join(
        f"## {section}\n待补充" for section in rule.required_sections if section != "结论"
    )
    body = (
        f"# {title}\n\n## 结论\n<请用一句话写清楚结论>\n\n{sections}\n\n## 原文摘录\n\n{excerpt}\n"
    )
    manifest = "\n".join(
        [
            "apiVersion: fde.asset/v1",
            f"kind: {kind}",
            "metadata:",
            f"  name: {name}",
            f"  title: {title}",
            '  summary: ""',
            "  tags: []",
            "spec:",
            f"  owner: {owner}",
            "  lifecycle: experimental",
            "  industry: []",
            "  applicability:",
            '    suitable: ""',
            '    notSuitable: ""',
            "  version: 0.1.0",
            "  source:",
            "    origin: legacy",
            f"    note: {legacy_note or filename}",
        ]
    )
    files = {
        rule.main_file: body,
        "asset.yaml": manifest,
        f"{ATTACHMENT_PREFIX}{safe_name(filename)}": ATTACHMENT_PLACEHOLDER,
    }
    result = create_draft(
        engine,
        principal,
        CandidateInput(
            kind=kind,
            name=name,
            title=title,
            scope=scope,
            department_code=department_code,
            engagement_slug=engagement_slug,
            customer_code=customer_code,
            origin="upload",
            source={"origin": "legacy", "note": legacy_note or filename},
            files=files,
        ),
    )
    if store is not None:
        store.put(result["candidate_id"], filename, content)
    result["extraction"] = {
        "status": extraction.status,
        "chars": len(extraction.text),
        "detail": extraction.detail,
    }
    return result


def delete_draft(
    engine: Engine, principal: Principal, candidate_id: str, store: BlobStore | None = None
) -> dict[str, Any]:
    """删掉一份还没提交的草稿。提交过的不能删——评审记录和仓库里的分支要留着追溯。"""
    candidate = get_candidate(engine, candidate_id)
    if candidate["created_by"] != principal.user_id:
        raise HarvestError("只能删除自己的草稿")
    if candidate["status"] != "draft":
        raise HarvestError("只有草稿状态的可以删除；已提交评审或已入库的要留着追溯")
    lead_id = (candidate.get("source") or {}).get("leadId", "")
    with engine.begin() as conn:
        conn.execute(
            harvest_candidates.delete().where(harvest_candidates.c.candidate_id == candidate_id)
        )
        restored = False
        if lead_id:
            # 草稿没了，那条线索等于没处理，放回工作台
            restored = bool(
                conn.execute(
                    update(asset_leads)
                    .where(asset_leads.c.lead_id == lead_id, asset_leads.c.status == "drafted")
                    .values(status="open")
                ).rowcount
            )
        record_event(
            conn,
            "candidate.deleted",
            {"candidate_id": candidate_id, "by": principal.user_id, "lead_restored": restored},
        )
    if store is not None:
        for filename in store.names(candidate_id):
            store.delete(candidate_id, filename)
    return {"candidate_id": candidate_id, "deleted": True, "lead_restored": restored}


def get_candidate(engine: Engine, candidate_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(
            select(harvest_candidates).where(harvest_candidates.c.candidate_id == candidate_id)
        ).first()
    if row is None:
        raise HarvestError(f"候选不存在：{candidate_id}")
    data = dict(row._mapping)
    data["files"] = json.loads(data.pop("files_json") or "{}")
    data["source"] = json.loads(data.pop("source_json") or "{}")
    data["checks"] = json.loads(data.pop("checks_json") or "{}")
    for key in ("created_at", "updated_at"):
        data[key] = data[key].isoformat()
    return data


def merge_files(engine: Engine, candidate_id: str, files: dict[str, str]) -> dict[str, Any]:
    """按文件名合并（PATCH 语义）。整份替换会把 asset.yaml 这类没传的文件删掉。"""
    candidate = get_candidate(engine, candidate_id)
    merged = dict(candidate["files"])
    merged.update(files)
    return update_candidate(engine, candidate_id, merged)


#: 草稿里附件的占位内容；真正的原件在 BlobStore 里，提交时才materialize 进仓库
ATTACHMENT_PLACEHOLDER = "<binary>"
ATTACHMENT_PREFIX = "attachments/"


def attach_file(
    engine: Engine,
    candidate_id: str,
    *,
    filename: str,
    content: bytes,
    store: BlobStore,
    size_limit: int = 50 * 1024 * 1024,
) -> dict[str, Any]:
    """给草稿加一个附件：原件存 BlobStore，文件清单里留占位，并回报能否提取正文。"""
    candidate = get_candidate(engine, candidate_id)
    if candidate["status"] != "draft":
        raise HarvestError("只有草稿可以加附件")
    if len(content) > size_limit:
        raise HarvestError(f"附件超过 {size_limit // 1024 // 1024}MB 上限")
    name = safe_name(filename)
    store.put(candidate_id, name, content)
    files = dict(candidate["files"])
    files[f"{ATTACHMENT_PREFIX}{name}"] = ATTACHMENT_PLACEHOLDER
    update_candidate(engine, candidate_id, files)
    extraction = extract_text(name, content)
    return {
        "path": f"{ATTACHMENT_PREFIX}{name}",
        "bytes": len(content),
        "text_extraction": extraction.status,
        "chars": len(extraction.text),
        "detail": extraction.detail,
    }


def detach_file(
    engine: Engine, candidate_id: str, *, filename: str, store: BlobStore
) -> dict[str, Any]:
    candidate = get_candidate(engine, candidate_id)
    if candidate["status"] != "draft":
        raise HarvestError("只有草稿可以删附件")
    name = safe_name(filename)
    files = {
        path: content
        for path, content in candidate["files"].items()
        if path != f"{ATTACHMENT_PREFIX}{name}"
    }
    store.delete(candidate_id, name)
    return update_candidate(engine, candidate_id, files)


def read_attachment(candidate_id: str, filename: str, store: BlobStore) -> bytes | None:
    return store.get(candidate_id, filename)


def update_candidate(engine: Engine, candidate_id: str, files: dict[str, str]) -> dict[str, Any]:
    with engine.begin() as conn:
        conn.execute(
            update(harvest_candidates)
            .where(harvest_candidates.c.candidate_id == candidate_id)
            .values(files_json=json.dumps(files, ensure_ascii=False), updated_at=_now())
        )
    return get_candidate(engine, candidate_id)


#: 允许前端直接改的元数据字段，其余字段仍靠编辑 asset.yaml
EDITABLE_META = ("title", "summary", "tags", "lifecycle", "industry", "suitable", "notSuitable")


def update_meta(engine: Engine, candidate_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    """按字段改 asset.yaml，前端不必自己拼 YAML。未知字段直接忽略。"""
    import yaml

    candidate = get_candidate(engine, candidate_id)
    if candidate["status"] != "draft":
        raise HarvestError("只有草稿可以修改")
    files = dict(candidate["files"])
    document = yaml.safe_load(files.get("asset.yaml") or "") or {}
    metadata = document.setdefault("metadata", {})
    spec = document.setdefault("spec", {})
    applicability = spec.setdefault("applicability", {})

    for key in ("title", "summary"):
        if key in meta:
            metadata[key] = str(meta[key] or "")
    if "tags" in meta:
        metadata["tags"] = [str(tag) for tag in (meta["tags"] or [])]
    if "lifecycle" in meta:
        spec["lifecycle"] = str(meta["lifecycle"] or "experimental")
    if "industry" in meta:
        spec["industry"] = [str(item) for item in (meta["industry"] or [])]
    for key in ("suitable", "notSuitable"):
        if key in meta:
            applicability[key] = str(meta[key] or "")

    files["asset.yaml"] = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    updated = update_candidate(engine, candidate_id, files)
    if "title" in meta:
        with engine.begin() as conn:
            conn.execute(
                update(harvest_candidates)
                .where(harvest_candidates.c.candidate_id == candidate_id)
                .values(title=str(meta["title"] or ""), updated_at=_now())
            )
        updated = get_candidate(engine, candidate_id)
    return updated


def run_checks(
    engine: Engine, candidate_id: str, scan_context: ScanContext | None = None
) -> dict[str, Any]:
    """格式校验 + 敏感信息扫描 + 必填项，结果写回候选。"""
    candidate = get_candidate(engine, candidate_id)
    files: dict[str, str] = candidate["files"]
    context = scan_context or ScanContext()
    rule = KIND_RULES[candidate["kind"]]
    findings: list[dict[str, str]] = []

    if candidate["kind"] == "Rule":
        main_text = next(iter(files.values()), "")
    else:
        main_text = files.get(rule.main_file, "")
        raw_yaml = files.get("asset.yaml", "")
        manifest, parse_findings = parse_manifest(raw_yaml)
        if manifest is None:
            findings.extend({"code": f.code, "message": f.message} for f in parse_findings)
        else:
            parsed = validate_asset(
                manifest,
                main_text=main_text,
                scope=candidate["scope"],
                directory_name=candidate["name"],
            )
            findings.extend({"code": f.code, "message": f.message} for f in parsed.findings)

    hits = scan_files(
        {name: text for name, text in files.items() if isinstance(text, str)},
        customer_names=context.customer_names,
        sensitive_terms=context.sensitive_terms,
    )
    scan_result = [
        {
            "code": h.code,
            "label": h.label,
            "severity": h.severity,
            "file": h.file,
            "line": h.line,
            "masked": h.masked,
        }
        for h in hits
    ]
    checks = {
        "format": findings,
        "scan": scan_result,
        "blocking": len(blocking(hits)) > 0 or bool(findings),
        "high_risk": len(blocking(hits)),
        "medium_risk": len([h for h in hits if h.severity == "medium"]),
        "checked_at": _now().isoformat(),
    }
    with engine.begin() as conn:
        conn.execute(
            update(harvest_candidates)
            .where(harvest_candidates.c.candidate_id == candidate_id)
            .values(checks_json=json.dumps(checks, ensure_ascii=False), updated_at=_now())
        )
    return checks


def submit(
    engine: Engine,
    principal: Principal,
    repo_port,
    candidate_id: str,
    *,
    target_repo: RepoRef,
    scan_context: ScanContext | None = None,
    medium_risk_confirmed: bool = False,
    blob_store: BlobStore | None = None,
) -> dict[str, Any]:
    """提交候选：建分支、提交文件、生成评审记录。高危必须清零。"""
    candidate = get_candidate(engine, candidate_id)
    if candidate["status"] != "draft":
        raise HarvestError("只有草稿状态的候选可以提交")
    checks = run_checks(engine, candidate_id, scan_context)
    if checks["high_risk"]:
        raise HarvestError("存在高危扫描结果，不能提交")
    if checks["format"]:
        raise HarvestError("格式校验未通过：" + checks["format"][0]["message"])
    if checks["medium_risk"] and not medium_risk_confirmed:
        raise HarvestError("存在中危结果，需要勾选确认后提交")

    rule = KIND_RULES[candidate["kind"]]
    prefix = _target_prefix(
        candidate["kind"], candidate["name"], target_repo.scope, candidate["files"]
    )
    files: dict[str, bytes] = {}
    for path, content in candidate["files"].items():
        if not isinstance(content, str):
            continue
        target = f"{prefix}/{path}" if prefix else path
        if content == ATTACHMENT_PLACEHOLDER and blob_store is not None:
            original = blob_store.get(candidate_id, path[len(ATTACHMENT_PREFIX) :])
            # 原件丢了也不拦提交，写回占位，评审时能看出来
            files[target] = original if original is not None else content.encode("utf-8")
        else:
            files[target] = content.encode("utf-8")
    branch = f"asset/{candidate['name']}-{candidate_id[:6]}"
    repo_port.create_branch(target_repo, branch)
    sha = repo_port.commit_files(
        target_repo,
        branch,
        files,
        f"feat(asset): 沉淀 {rule.label}「{candidate['title']}」",
        author_name=principal.display_name or principal.user_id,
        author_email=f"{principal.user_id}@fde.local",
    )
    review_id = uuid.uuid4().hex[:12]
    summary = "\n".join(
        [
            f"来源：{candidate['origin']}",
            f"作用域：{candidate['scope']}",
            f"扫描：高危 {checks['high_risk']} · 中危 {checks['medium_risk']}",
            f"提交人：{principal.user_id}",
        ]
    )
    with engine.begin() as conn:
        conn.execute(
            asset_reviews.insert().values(
                review_id=review_id,
                candidate_id=candidate_id,
                repo=target_repo.name,
                branch=branch,
                status="open",
                scope=candidate["scope"],
                summary=summary,
                submitted_by=principal.user_id,
            )
        )
        conn.execute(
            update(harvest_candidates)
            .where(harvest_candidates.c.candidate_id == candidate_id)
            .values(
                status="submitted",
                review_id=review_id,
                updated_at=_now(),
                medium_risk_confirmed=medium_risk_confirmed,
            )
        )
        record_event(
            conn,
            "candidate.submitted",
            {
                "candidate_id": candidate_id,
                "review_id": review_id,
                "repo": target_repo.name,
                "branch": branch,
                "scope": candidate["scope"],
            },
        )
    return {"review_id": review_id, "branch": branch, "commit": sha, "checks": checks}


def decide(
    engine: Engine,
    principal: Principal,
    repo_port,
    review_id: str,
    *,
    approve: bool,
    target_repo: RepoRef,
    note: str = "",
) -> dict[str, Any]:
    """评审决定：通过则合并到默认分支，驳回则关闭分支。"""
    with engine.connect() as conn:
        review = conn.execute(
            select(asset_reviews).where(asset_reviews.c.review_id == review_id)
        ).first()
    if review is None:
        raise HarvestError(f"评审不存在：{review_id}")
    if review.status != "open":
        return {"review_id": review_id, "status": review.status, "idempotent": True}

    merged_sha = ""
    if approve:
        merged_sha = repo_port.merge_branch(target_repo, review.branch, f"merge {review.branch}")
    else:
        repo_port.delete_branch(target_repo, review.branch)

    status = "merged" if approve else "rejected"
    with engine.begin() as conn:
        conn.execute(
            update(asset_reviews)
            .where(asset_reviews.c.review_id == review_id)
            .values(status=status, decided_by=principal.user_id, decided_at=_now(), note=note)
        )
        conn.execute(
            update(harvest_candidates)
            .where(harvest_candidates.c.candidate_id == review.candidate_id)
            # 打回的候选回到草稿状态，作者才能按意见改完再交；评审记录保留 rejected 供追溯
            .values(status="draft" if status == "rejected" else status, updated_at=_now())
        )
        record_event(
            conn,
            f"candidate.{status}",
            {
                "review_id": review_id,
                "candidate_id": review.candidate_id,
                "repo": review.repo,
                "branch": review.branch,
                "commit": merged_sha,
            },
        )
    return {"review_id": review_id, "status": status, "commit": merged_sha}


def _target_prefix(kind: str, name: str, scope: str, files: dict[str, str]) -> str:
    rule = KIND_RULES[kind]
    if kind == "Rule":
        return "rules"
    if scope == "engagement":
        return (
            f".agents/skills/{name}"
            if kind == "Skill"
            else f".fde/assets/{rule.directory.split('/')[-1]}/{name}"
        )
    if kind == "Sop":
        layer = "l2"
        raw = files.get("asset.yaml", "")
        for line in raw.splitlines():
            if line.strip().startswith("layer:"):
                layer = line.split(":", 1)[1].strip().lower()
        return f"sops/{layer}/{name}"
    return f"{rule.directory}/{name}"


def _slugify(text: str, *, fallback: str | None = None) -> str:
    """中文标题没有可用的 ASCII 片段时返回 fallback（默认随机短名）。"""
    import re

    ascii_text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    ascii_text = re.sub(r"-{2,}", "-", ascii_text)
    if ascii_text and not ascii_text.replace("-", "").isdigit():
        return ascii_text[:48]
    if fallback is not None:
        return fallback
    return "item-" + uuid.uuid4().hex[:8]
