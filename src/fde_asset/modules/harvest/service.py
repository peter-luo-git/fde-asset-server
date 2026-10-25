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
from fde_asset.modules.asset.indexer import asset_id_for_candidate
from fde_asset.modules.asset.manifest import (
    ASSET_ROOT,
    KIND_RULES,
    NAME_PATTERN,
    SHORT_BY_KIND,
    parse_manifest,
    validate_asset,
    visibility_entry,
)
from fde_asset.modules.harvest.templates import render_template
from fde_asset.platform.blobs import BlobStore, safe_name
from fde_asset.platform.files import FileStore
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
    #: 修订哪份已入库资产：入库时写回那份资产，编号不变、版本加一
    revises_asset_id: str = ""
    #: 除了归属作用域，还想让谁看见；入库时评审人有权放的直接生效，其余转成可见范围申请
    visibility: list[str] = field(default_factory=list)


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


def landed_asset_id(candidate: Any) -> str:
    """草稿入库后是哪份资产：修订稿是被修订的那份，其余由草稿编号算出来。"""
    get = (
        candidate.get
        if isinstance(candidate, dict)
        else lambda key, default="": getattr(candidate, key, default)
    )
    return get("revises_asset_id", "") or asset_id_for_candidate(get("candidate_id"))


def clean_visibility(visibility: Any) -> list[str]:
    """起草时选的额外可见范围：校验写法、去重；可以为空。"""
    from fde_asset.modules.asset.manifest import VISIBILITY_PATTERN

    if visibility in (None, ""):
        return []
    if not isinstance(visibility, list) or len(visibility) > 50:
        raise HarvestError("可见范围要是一个列表")
    items = [str(item).strip() for item in visibility if str(item).strip()]
    for item in items:
        if not VISIBILITY_PATTERN.match(item):
            raise HarvestError(
                f"可见范围写法不对：{item}。应是 company，或 department:/customer:/engagement:/user: 加代号"
            )
    return list(dict.fromkeys(items))


def set_visibility(engine: Engine, candidate_id: str, visibility: Any) -> dict[str, Any]:
    items = clean_visibility(visibility)
    with engine.begin() as conn:
        conn.execute(
            update(harvest_candidates)
            .where(harvest_candidates.c.candidate_id == candidate_id)
            .values(visibility_json=json.dumps(items, ensure_ascii=False), updated_at=_now())
        )
    return get_candidate(engine, candidate_id)


def create_draft(engine: Engine, principal: Principal, data: CandidateInput) -> dict[str, Any]:
    if data.kind not in KIND_RULES:
        raise HarvestError(f"不支持的 kind：{data.kind}")
    extra_visibility = clean_visibility(data.visibility)
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
    if data.scope == "engagement":
        if not data.engagement_slug:
            raise HarvestError("项目级候选必须指定 engagement_slug")
        # 和客户级一个道理：不在项目里的人不能往这个项目的仓库里写东西
        if not principal.is_admin and data.engagement_slug not in principal.engagement_slugs:
            raise HarvestError(f"你不是项目 {data.engagement_slug} 的成员，不能为它沉淀资产")
    if data.scope == "department" and not data.department_code:
        raise HarvestError("部门级候选必须指定 department_code")
    if data.scope == "customer":
        if not data.customer_code:
            raise HarvestError("客户级候选必须指定 customer_code")
        # 只有参与这个客户项目的人才写得了它的资产；否则任何人都能往别的客户仓库里塞东西
        if not principal.is_admin and data.customer_code not in principal.customer_codes:
            raise HarvestError(f"你没有参与客户 {data.customer_code} 的项目，不能为它沉淀资产")

    if data.scope not in {"company", "department", "customer", "engagement", "user"}:
        raise HarvestError("归属范围只能是 company、department、customer、engagement、user")
    if data.scope == "user" and data.kind != "Skill":
        raise HarvestError("个人专用目前只支持技能")
    if data.scope == "user":
        # 个人专用：负责人是本人；部门记下来，带脚本时由本部门评审员看
        data = replace(data, department_code=principal.department_code)
    owner = (
        f"user:{principal.user_id}"
        if data.scope == "user" or not principal.department_code
        else f"department:{principal.department_code}"
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
                revises_asset_id=data.revises_asset_id,
                visibility_json=json.dumps(extra_visibility, ensure_ascii=False),
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
    if kind == "Skill":
        # 技能靠开头的 name、description 被 Agent 发现，没有它入库了也用不上
        from fde_asset.modules.asset.skill_package import with_frontmatter

        body = with_frontmatter(body, name=name, description="")
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


def draft_from_skill_package(
    engine: Engine,
    principal: Principal,
    package: Any,
    *,
    title: str = "",
    scope: str = "user",
    department_code: str = "",
    engagement_slug: str = "",
    customer_code: str = "",
    origin: str = "upload",
    source: dict[str, Any] | None = None,
    revises_asset_id: str = "",
) -> dict[str, Any]:
    """整目录导入技能（上传文件夹/zip，或从公开技能库安装）：目录原样进草稿，补上 asset.yaml。

    标识和标题取 SKILL.md 开头的 name；一句话结论、适用取 description，不适用要人补。
    """
    import yaml

    from fde_asset.modules.asset import skill_package

    files = dict(package.files)
    front = skill_package.parse_frontmatter(files.get("SKILL.md", "")) or {}
    name = str(front.get("name") or "").strip()
    if not name or not NAME_PATTERN.match(name) or len(name) > 64:
        name = generate_name(engine, "Skill")
        files["SKILL.md"] = skill_package.with_frontmatter(
            files["SKILL.md"], name=name, description=str(front.get("description") or "")
        )
    description = " ".join(str(front.get("description") or "").split())
    origin_note = dict(source or {})
    if package.rejected:
        origin_note["rejected_files"] = package.rejected[:50]
    owner = (
        f"user:{principal.user_id}"
        if scope == "user" or not principal.department_code
        else f"department:{principal.department_code}"
    )
    manifest = {
        "apiVersion": "fde.asset/v1",
        "kind": "Skill",
        "metadata": {
            "name": name,
            "title": (title or name)[:50],
            "summary": description[:120],
            "tags": [],
        },
        "spec": {
            "owner": owner,
            "lifecycle": "experimental",
            "industry": [],
            "applicability": {
                "suitable": description[:2000],
                "notSuitable": "",
            },
            "version": "0.1.0",
            "source": {k: v for k, v in origin_note.items() if k != "rejected_files"},
        },
    }
    if not manifest["spec"]["source"]:
        manifest["spec"].pop("source")
    files.pop("asset.yaml", None)
    files["asset.yaml"] = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    return create_draft(
        engine,
        principal,
        CandidateInput(
            kind="Skill",
            name=name,
            title=(title or name)[:50],
            scope=scope,
            department_code=department_code,
            engagement_slug=engagement_slug,
            customer_code=customer_code,
            origin=origin,
            source=origin_note,
            files=files,
            revises_asset_id=revises_asset_id,
        ),
    )


def personal_auto_approvable(candidate: dict[str, Any]) -> bool:
    """个人专用、不带脚本、自己写的技能免人工评审（2026-10-25）；外部安装的不免。"""
    if candidate.get("kind") != "Skill" or candidate.get("scope") != "user":
        return False
    if candidate.get("origin") == "install":
        return False
    report = ((candidate.get("checks") or {}).get("skill") or {}).get("report") or {}
    return not report.get("scripts") and report.get("level") != "high"


def set_executables(
    repo_port: Any,
    target_repo: Any,
    branch: str,
    candidate: dict[str, Any],
    executables: list[str],
    *,
    engine: Engine | None = None,
) -> None:
    """评审通过时把允许执行的脚本写进分支上的 asset.yaml（spec.executables），再合并。"""
    import yaml

    revised = _revised_row(engine, candidate) if engine is not None else None
    prefix = revised.path if revised is not None else f"{ASSET_ROOT}/{landed_asset_id(candidate)}"
    path = f"{prefix}/asset.yaml"
    raw = repo_port.read_file(target_repo, path, ref=branch)
    document = yaml.safe_load(raw) or {}
    spec = document.setdefault("spec", {})
    if executables:
        spec["executables"] = sorted(executables)
    else:
        spec.pop("executables", None)
    repo_port.commit_files(
        target_repo,
        branch,
        {path: yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode("utf-8")},
        "chore(asset): 评审定下技能里可以执行的脚本",
        author_name="资产中心",
        author_email="asset-center@fde.local",
    )


def delete_draft(
    engine: Engine,
    principal: Principal,
    candidate_id: str,
    store: BlobStore | None = None,
    *,
    as_owner: bool = False,
) -> dict[str, Any]:
    """删掉一份还没提交的草稿。提交过的不能删——评审记录和仓库里的分支要留着追溯。

    `as_owner`：这是针对调用人负责的资产起的修订草稿，虽然不是他建的，也由他处置。
    """
    candidate = get_candidate(engine, candidate_id)
    if candidate["created_by"] != principal.user_id and not as_owner:
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
    data["visibility"] = json.loads(data.pop("visibility_json", None) or "[]")
    data["revises_asset_id"] = data.get("revises_asset_id") or ""
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
EDITABLE_META = (
    "title",
    "summary",
    "tags",
    "lifecycle",
    "industry",
    "suitable",
    "notSuitable",
    "sop",
)


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

    # SOP：层级和上层流程。L1 不继承谁；L2 继承一份 L1，L3 继承一份 L2（写上层流程的名字）
    sop = meta.get("sop")
    if isinstance(sop, dict) and document.get("kind") == "Sop":
        if "layer" in sop:
            layer = str(sop["layer"] or "L1")
            if layer not in {"L1", "L2", "L3"}:
                raise HarvestError("层级只能是 L1、L2、L3")
            spec["layer"] = layer
        if "extends" in sop:
            spec["extends"] = str(sop["extends"] or "").strip()
        if spec.get("layer", "L1") == "L1" or not spec.get("extends"):
            spec.pop("extends", None)

    # 应用：项目来源、成熟度、代码仓库、怎么跑、演示入口。登记一个已经在别处跑着的应用，
    # 填的就是这几项（运行方式选 url，写上演示地址和在哪能打开）
    app = meta.get("app")
    if isinstance(app, dict) and document.get("kind") == "Application":
        for key, target in (
            ("source_type", "sourceType"),
            ("maturity", "maturity"),
            ("repo", "repo"),
        ):
            if key in app:
                spec[target] = str(app[key] or "")
        if "runtime_type" in app:
            runtime = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
            runtime["type"] = str(app["runtime_type"] or "url")
            spec["runtime"] = runtime
        if isinstance(app.get("demo"), dict):
            demo = spec.get("demo") if isinstance(spec.get("demo"), dict) else {}
            for key in ("network", "url", "account", "reachable_from", "note"):
                if key in app["demo"]:
                    demo[key] = str(app["demo"][key] or "")
            spec["demo"] = demo

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
    sop_hints: list[dict[str, Any]] = []
    skill_report: dict[str, Any] | None = None

    files = _normalized_files(candidate, files)
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
        if candidate["kind"] == "Skill":
            from fde_asset.modules.asset import skill_checks, skill_package

            findings.extend(skill_package.frontmatter_findings(main_text, name=candidate["name"]))
            extra = manifest.spec.model_extra or {}
            front = skill_package.parse_frontmatter(main_text) or {}
            selftest = str(extra.get("selftest") or front.get("selftest") or "")
            package_files = skill_checks.skill_files(files)
            report = skill_package.scan(package_files)
            trial = skill_checks.ensure_trial(engine, candidate_id, package_files, selftest)
            skill_report = {
                "report": report,
                "trial": trial,
                "model": skill_checks.model_for(engine, package_files),
                "hints": skill_package.writing_hints(main_text),
                "default_executables": skill_package.default_executables(report, trial),
                "tickable": skill_package.allowed_to_tick(report, trial),
                "rejected": (candidate.get("source") or {}).get("rejected_files", []),
            }
        if candidate["kind"] == "Sop":
            # 步骤格式检查：认不出步骤是硬伤（流程没法执行），其余是写作提醒，不挡提交
            from fde_asset.modules.asset import sop as sop_module

            extra = manifest.spec.model_extra or {}
            for item in sop_module.lint(
                main_text,
                layer=str(extra.get("layer") or "L1"),
                extends=str(extra.get("extends") or ""),
            ):
                if item["level"] == "error" and "上层流程" not in item["message"]:
                    findings.append({"code": "sop_steps_invalid", "message": item["message"]})
                else:
                    sop_hints.append(item)
        taken = _name_taken(
            engine,
            owner=manifest.spec.owner,
            kind=candidate["kind"],
            name=candidate["name"],
            asset_id=landed_asset_id(candidate),
        )
        if taken:
            findings.append(
                {
                    "code": "name_duplicate",
                    "message": f"{manifest.spec.owner} 名下已经有一份同类型、标识也是 "
                    f"{candidate['name']} 的资产，请换一个标识",
                }
            )

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
        **({"sop_hints": sop_hints} if candidate["kind"] == "Sop" else {}),
        **({"skill": skill_report} if skill_report is not None else {}),
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
    file_store: FileStore | None = None,
) -> dict[str, Any]:
    """提交候选：建分支、提交文件、生成评审记录。高危必须清零。

    仓库里只进文字：正文和 asset.yaml 写到 `assets/<资产编号>/`；附件原件存进文件存储，
    asset.yaml 里记文件名和哈希。
    """
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
    asset_id = landed_asset_id(candidate)
    prefix = f"{ASSET_ROOT}/{asset_id}"
    revised = _revised_row(engine, candidate)
    if revised is not None:
        # 写回原资产自己的目录；旧布局导入的资产不一定在 assets/<编号> 下
        prefix = revised.path
    files: dict[str, bytes] = {}
    attachments: list[dict[str, Any]] = []
    for path, content in _normalized_files(candidate, candidate["files"]).items():
        if not isinstance(content, str) or path == "asset.yaml":
            continue
        if path.startswith(ATTACHMENT_PREFIX):
            name = path[len(ATTACHMENT_PREFIX) :]
            original = blob_store.get(candidate_id, name) if blob_store is not None else None
            if original is None or file_store is None:
                raise HarvestError(f"附件 {name} 的原件找不到了，请删掉它重新上传")
            # 草稿原件平时就在同一个文件存储里，这一步只是登记；不在的（旧数据、测试）才真的写
            sha256 = blob_store.sha256(candidate_id, name) if blob_store is not None else None
            if sha256 is None or not file_store.exists(sha256):
                sha256 = file_store.put(original)
            attachments.append({"name": name, "sha256": sha256, "bytes": len(original)})
            continue
        files[f"{prefix}/{path}"] = content.encode("utf-8")
    files[f"{prefix}/asset.yaml"] = _landed_manifest(
        candidate, asset_id=asset_id, attachments=attachments, revised=revised
    ).encode("utf-8")
    branch = f"asset/{candidate['name']}-{candidate_id[:6]}"
    repo_port.create_branch(target_repo, branch)
    sha = repo_port.commit_files(
        target_repo,
        branch,
        files,
        (
            f"feat(asset): 修订 {rule.label}「{candidate['title']}」"
            if revised is not None
            else f"feat(asset): 沉淀 {rule.label}「{candidate['title']}」"
        ),
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


def _normalized_files(candidate: dict[str, Any], files: dict[str, str]) -> dict[str, str]:
    """把草稿的文件清单整理成入库的形状。

    规范以前是孤零零一个 `<标识>.md`、没有 asset.yaml；现在和别的资产一样是
    主文件加 asset.yaml。旧草稿在这里补齐，新草稿原样返回。
    """
    if candidate["kind"] != "Rule":
        return files
    main_file = KIND_RULES["Rule"].main_file
    result = dict(files)
    if main_file not in result:
        legacy = next((path for path in result if path.endswith(".md")), None)
        if legacy is not None:
            result[main_file] = result.pop(legacy)
    if "asset.yaml" not in result:
        owner = f"user:{candidate['created_by']}"
        result["asset.yaml"] = render_template(
            "Rule", name=candidate["name"], title=candidate["title"], owner=owner
        )["asset.yaml"]
    return result


def _revised_row(engine: Engine, candidate: dict[str, Any]) -> Any:
    """修订稿要写回的那份资产；不是修订稿返回 None。资产没了就不能当修订稿交。"""
    asset_id = candidate.get("revises_asset_id") or ""
    if not asset_id:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            select(assets).where(assets.c.asset_id == asset_id, assets.c.deleted_at.is_(None))
        ).first()
    if row is None:
        raise HarvestError("要修订的资产已经不在了；可以删掉这份草稿，另起一份新资产")
    return row


def bump_version(current: str, drafted: str) -> str:
    """修订稿的版本号：草稿里写得比现在大就用它，否则在现在的基础上加一个小版本。"""

    def parts(value: str) -> tuple[int, int, int] | None:
        pieces = str(value or "").strip().split(".")
        if len(pieces) != 3 or not all(piece.isdigit() for piece in pieces):
            return None
        return int(pieces[0]), int(pieces[1]), int(pieces[2])

    now, wanted = parts(current), parts(drafted)
    if now is None:
        return drafted or "0.1.0"
    if wanted is not None and wanted > now:
        return drafted
    return f"{now[0]}.{now[1] + 1}.0"


def _landed_manifest(
    candidate: dict[str, Any],
    *,
    asset_id: str,
    attachments: list[dict[str, Any]],
    revised: Any = None,
) -> str:
    """入库用的 asset.yaml：编号、可见范围、附件清单以这里算出来的为准，其余照草稿。

    修订稿：可见范围沿用资产现在的（调整可见范围另有入口），版本号在现在的基础上加一。
    """
    import yaml

    raw = _normalized_files(candidate, candidate["files"]).get("asset.yaml", "")
    document = yaml.safe_load(raw) or {}
    metadata = document.setdefault("metadata", {})
    spec = document.setdefault("spec", {})
    # 编号放在最前面，打开文件第一眼就能看到
    document["metadata"] = {"id": asset_id, **{k: v for k, v in metadata.items() if k != "id"}}
    if revised is not None:
        spec["visibility"] = json.loads(revised.visibility_json or "[]") or [
            visibility_entry(
                revised.scope,
                department_code=revised.department_code or "",
                customer_code=revised.customer_code or "",
                engagement_slug=revised.engagement_slug or "",
            )
        ]
        spec["version"] = bump_version(revised.version, str(spec.get("version") or ""))
    else:
        spec["visibility"] = [
            visibility_entry(
                candidate["scope"],
                department_code=candidate.get("department_code") or "",
                customer_code=candidate.get("customer_code") or "",
                engagement_slug=candidate.get("engagement_slug") or "",
                user=candidate.get("created_by") or "",
            )
        ]
    if attachments:
        spec["attachments"] = attachments
    else:
        spec.pop("attachments", None)
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)


def _name_taken(engine: Engine, *, owner: str, kind: str, name: str, asset_id: str) -> bool:
    """同一个负责人名下，同类型的标识是不是已经被别的资产占了。"""
    with engine.connect() as conn:
        row = conn.execute(
            select(assets.c.asset_id).where(
                assets.c.owner_ref == owner,
                assets.c.kind == kind,
                assets.c.name == name,
                assets.c.asset_id != asset_id,
                assets.c.deleted_at.is_(None),
            )
        ).first()
    return row is not None


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


def open_revision(engine: Engine, asset_id: str) -> dict[str, Any] | None:
    """这份资产有没有还没入库的修订稿（草稿或评审中）。"""
    with engine.connect() as conn:
        row = conn.execute(
            select(harvest_candidates.c.candidate_id)
            .where(
                harvest_candidates.c.revises_asset_id == asset_id,
                harvest_candidates.c.status.in_(["draft", "submitted"]),
            )
            .order_by(harvest_candidates.c.created_at.desc())
        ).first()
    return get_candidate(engine, row.candidate_id) if row else None


def draft_revision(
    engine: Engine,
    principal: Principal,
    repo_port,
    asset_row: Any,
    *,
    blob_store: BlobStore | None = None,
    file_store: FileStore | None = None,
    origin: str = "edit",
    note: str = "",
) -> dict[str, Any]:
    """在网页上修改一份已入库资产：起一份修订稿，内容是资产现在的全部文件。

    改完照常提交评审，通过后写回这份资产（编号不变、版本加一）。同一份资产同时只留一份
    没入库的修订稿：已经有了就返回那一份，免得两个人各改各的、后入库的把先入库的冲掉。
    """
    import yaml

    existing = open_revision(engine, asset_row.asset_id)
    if existing is not None:
        return existing
    repo = RepoRef(name=asset_row.repo)
    files: dict[str, str] = {}
    for entry in repo_port.list_tree(repo, asset_row.path):
        relative = entry.path[len(asset_row.path) + 1 :]
        if not relative:
            continue
        content = repo_port.read_file(repo, entry.path)
        try:
            files[relative] = content.decode("utf-8")
        except UnicodeDecodeError:
            continue  # 仓库里只有文字；万一有别的，修订稿里不带
    manifest = yaml.safe_load(files.get("asset.yaml") or "") or {}
    spec = manifest.get("spec") if isinstance(manifest, dict) else {}
    attachments = (spec or {}).get("attachments") or []
    if isinstance(spec, dict):
        # 附件清单和可见范围入库时由服务端算，草稿里不留，免得被手改
        spec.pop("attachments", None)
        files["asset.yaml"] = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    for attachment in attachments:
        name = str(attachment.get("name") or "")
        if name:
            files[f"{ATTACHMENT_PREFIX}{safe_name(name)}"] = ATTACHMENT_PLACEHOLDER
    candidate = create_draft(
        engine,
        principal,
        CandidateInput(
            kind=asset_row.kind,
            name=asset_row.name,
            title=asset_row.title,
            scope=asset_row.scope,
            department_code=asset_row.department_code or "",
            engagement_slug=asset_row.engagement_slug or "",
            customer_code=asset_row.customer_code or "",
            origin=origin,
            source={
                "origin": origin,
                "revises": asset_row.asset_id,
                **({"note": note} if note else {}),
            },
            files=files,
            revises_asset_id=asset_row.asset_id,
        ),
    )
    if blob_store is not None and file_store is not None:
        for attachment in attachments:
            content = file_store.get(str(attachment.get("sha256") or ""))
            if content is not None and attachment.get("name"):
                blob_store.put(candidate["candidate_id"], str(attachment["name"]), content)
    return get_candidate(engine, candidate["candidate_id"])
