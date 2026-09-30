"""索引流水线：discover → read → parse → validate → relate → persist。

借鉴 Backstage 的处理器链形状；每一步的错误都落 asset_index_findings，界面可见。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine

from fde_asset.core.db import asset_index_findings, asset_relations, assets, record_event
from fde_asset.platform.extract import extract_text
from fde_asset.platform.refs.wiki import parse_refs
from fde_asset.platform.repo.ports import RepoRef
from fde_asset.modules.asset.manifest import (
    KIND_RULES,
    Finding,
    parse_manifest,
    split_sections,
    validate_asset,
)

NAMESPACE = uuid.UUID("7f9d1c3e-6a25-4a7f-9a9f-9a0e0d1f2b34")

COMPANY_LAYOUT: tuple[tuple[str, str], ...] = (
    ("rules/", "Rule"),
    ("sops/l1/", "Sop"),
    ("sops/l2/", "Sop"),
    ("sops/l3/", "Sop"),
    ("skills/", "Skill"),
    ("knowledge/solutions/", "Solution"),
    ("knowledge/implementations/", "Implementation"),
    ("knowledge/cases/", "Case"),
    ("knowledge/experiences/", "Experience"),
)

ENGAGEMENT_LAYOUT: tuple[tuple[str, str], ...] = (
    (".agents/skills/", "Skill"),
    (".fde/assets/sops/", "Sop"),
    (".fde/assets/skills/", "Skill"),
    (".fde/assets/solutions/", "Solution"),
    (".fde/assets/implementations/", "Implementation"),
    (".fde/assets/cases/", "Case"),
    (".fde/assets/experiences/", "Experience"),
)


@dataclass
class DiscoveredAsset:
    kind: str
    name: str
    directory: str
    files: dict[str, str] = field(default_factory=dict)  # 相对资产目录的路径 → 仓库内全路径


@dataclass
class IndexReport:
    repo: str
    head: str = ""
    indexed: int = 0
    invalid: int = 0
    removed: int = 0
    findings: list[tuple[str, str]] = field(default_factory=list)


def asset_id_for(
    scope: str, department_code: str, engagement_slug: str, kind: str, name: str
) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{scope}|{department_code}|{engagement_slug}|{kind}|{name}"))


def discover(repo: RepoRef, paths: Iterable[str]) -> list[DiscoveredAsset]:
    layout = ENGAGEMENT_LAYOUT if repo.scope == "engagement" else COMPANY_LAYOUT
    found: dict[tuple[str, str], DiscoveredAsset] = {}
    for path in paths:
        for prefix, kind in layout:
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            if kind == "Rule":
                if "/" in rest or not rest.endswith(".md"):
                    continue
                name = rest[:-3]
                key = (kind, name)
                found.setdefault(key, DiscoveredAsset(kind, name, prefix.rstrip("/")))
                found[key].files[rest] = path
                break
            if "/" not in rest:
                continue
            name, _, relative = rest.partition("/")
            if not name or not relative:
                continue
            key = (kind, name)
            found.setdefault(key, DiscoveredAsset(kind, name, f"{prefix}{name}"))
            found[key].files[relative] = path
            break
    return list(found.values())


def _clean(value: object) -> str:
    return "" if value is None else str(value)


def index_repository(
    engine: Engine, repo_port, repo: RepoRef, *, text_limit: int = 200_000
) -> IndexReport:
    """索引一个仓库的全部资产，返回统计与错误清单。"""
    report = IndexReport(repo=repo.name)
    report.head = repo_port.get_head(repo)
    entries = [entry.path for entry in repo_port.list_tree(repo)]
    discovered = discover(repo, entries)
    seen_ids: set[str] = set()
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(delete(asset_index_findings).where(asset_index_findings.c.repo == repo.name))
        for item in discovered:
            findings: list[Finding] = []
            main_file = KIND_RULES[item.kind].main_file
            aid = asset_id_for(
                repo.scope, repo.department_code, repo.engagement_slug, item.kind, item.name
            )
            seen_ids.add(aid)

            if item.kind == "Rule":
                rule_path = next(iter(item.files.values()))
                text = repo_port.read_file(repo, rule_path).decode("utf-8", errors="replace")
                sections = split_sections(text)
                title = text.splitlines()[0].lstrip("# ").strip() if text.strip() else item.name
                summary = sections.get("结论", "").split("\n")[0][:120]
                values = dict(
                    asset_id=aid,
                    scope=repo.scope,
                    department_code=repo.department_code,
                    engagement_slug=repo.engagement_slug,
                    repo=repo.name,
                    path=rule_path,
                    kind="Rule",
                    name=item.name,
                    title=title or item.name,
                    summary=summary,
                    conclusion_md=sections.get("结论", ""),
                    applicability_json="{}",
                    tags_json="[]",
                    industry_json="[]",
                    owner_ref="department:platform",
                    owner_kind="department",
                    owner_value="platform",
                    lifecycle="stable",
                    quality="bronze",
                    nature="rule",
                    version="1.0.0",
                    replaced_by="",
                    source_json="{}",
                    kind_spec_json="{}",
                    attachments_json="[]",
                    content_text=text[:text_limit],
                    commit_sha=report.head,
                    restricted=False,
                    valid=True,
                    updated_at=now,
                    deleted_at=None,
                )
                _upsert(conn, aid, values, now)
                report.indexed += 1
                continue

            if main_file not in item.files:
                _finding(conn, repo, item, "main_file_missing", f"缺少主文件 {main_file}")
                report.invalid += 1
                report.findings.append((item.name, "main_file_missing"))
                _mark_invalid(conn, aid)
                continue
            if "asset.yaml" not in item.files:
                _finding(conn, repo, item, "manifest_missing", "缺少 asset.yaml")
                report.invalid += 1
                report.findings.append((item.name, "manifest_missing"))
                _mark_invalid(conn, aid)
                continue

            main_text = repo_port.read_file(repo, item.files[main_file]).decode(
                "utf-8", errors="replace"
            )
            raw_yaml = repo_port.read_file(repo, item.files["asset.yaml"]).decode(
                "utf-8", errors="replace"
            )
            manifest, parse_findings = parse_manifest(raw_yaml)
            if manifest is None:
                for finding in parse_findings:
                    _finding(conn, repo, item, finding.code, finding.message)
                report.invalid += 1
                report.findings.append(
                    (item.name, parse_findings[0].code if parse_findings else "manifest_invalid")
                )
                _mark_invalid(conn, aid)
                continue

            parsed = validate_asset(
                manifest, main_text=main_text, scope=repo.scope, directory_name=item.name
            )
            findings.extend(parsed.findings)

            attachments: list[dict] = []
            extracted_chunks: list[str] = []
            for relative, full_path in sorted(item.files.items()):
                if not relative.startswith("attachments/"):
                    continue
                blob = repo_port.read_file(repo, full_path)
                extraction = extract_text(relative, blob, limit=text_limit)
                attachments.append(
                    {
                        "path": relative,
                        "bytes": len(blob),
                        "text_extraction": extraction.status,
                        "detail": extraction.detail,
                    }
                )
                if extraction.status == "ok":
                    extracted_chunks.append(extraction.text)

            if findings and any(f.severity == "error" for f in findings):
                for finding in findings:
                    _finding(conn, repo, item, finding.code, finding.message)
                report.invalid += 1
                report.findings.append((item.name, findings[0].code))

            spec_extra = dict(manifest.spec.model_extra or {})
            owner_kind, _, owner_value = manifest.spec.owner.partition(":")
            content_text = "\n".join([main_text, *extracted_chunks])[:text_limit]
            values = dict(
                asset_id=aid,
                scope=repo.scope,
                department_code=repo.department_code,
                engagement_slug=repo.engagement_slug,
                repo=repo.name,
                path=item.directory,
                kind=manifest.kind,
                name=manifest.metadata.name,
                title=manifest.metadata.title,
                summary=manifest.metadata.summary,
                conclusion_md=parsed.sections.get("结论", ""),
                applicability_json=json.dumps(
                    manifest.spec.applicability.model_dump(), ensure_ascii=False
                ),
                tags_json=json.dumps(manifest.metadata.tags, ensure_ascii=False),
                industry_json=json.dumps(manifest.spec.industry, ensure_ascii=False),
                owner_ref=manifest.spec.owner,
                owner_kind=owner_kind,
                owner_value=owner_value,
                lifecycle=manifest.spec.lifecycle,
                quality=manifest.spec.quality,
                nature=_clean(manifest.spec.nature) or KIND_RULES[manifest.kind].nature,
                version=manifest.spec.version,
                replaced_by=manifest.spec.replacedBy,
                source_json=json.dumps(manifest.spec.source.model_dump(), ensure_ascii=False),
                kind_spec_json=json.dumps(spec_extra, ensure_ascii=False),
                attachments_json=json.dumps(attachments, ensure_ascii=False),
                content_text=content_text,
                commit_sha=report.head,
                restricted=bool(manifest.spec.restricted),
                valid=parsed.valid,
                updated_at=now,
                deleted_at=None,
            )
            _upsert(conn, aid, values, now)
            if parsed.valid:
                report.indexed += 1

            conn.execute(delete(asset_relations).where(asset_relations.c.from_asset_id == aid))
            for relation in manifest.spec.relations:
                conn.execute(
                    asset_relations.insert().values(
                        from_asset_id=aid,
                        to_ref=relation.target,
                        type=relation.type,
                        source="metadata",
                    )
                )
            for ref in parse_refs(main_text):
                conn.execute(
                    asset_relations.insert().values(
                        from_asset_id=aid,
                        to_ref=f"{ref.short_kind}/{ref.name}",
                        type="relatedTo",
                        source="body",
                    )
                )

        existing = (
            conn.execute(
                select(assets.c.asset_id).where(
                    assets.c.repo == repo.name, assets.c.deleted_at.is_(None)
                )
            )
            .scalars()
            .all()
        )
        for asset_id in existing:
            if asset_id not in seen_ids:
                conn.execute(
                    update(assets).where(assets.c.asset_id == asset_id).values(deleted_at=now)
                )
                report.removed += 1

        record_event(
            conn,
            "asset.indexed",
            {
                "repo": repo.name,
                "scope": repo.scope,
                "head": report.head,
                "indexed": report.indexed,
                "invalid": report.invalid,
                "removed": report.removed,
            },
        )
        if report.invalid:
            record_event(conn, "asset.invalid_found", {"repo": repo.name, "count": report.invalid})
    return report


def _upsert(conn, asset_id: str, values: dict, now: datetime) -> None:
    exists = conn.execute(select(assets.c.asset_id).where(assets.c.asset_id == asset_id)).first()
    if exists:
        conn.execute(update(assets).where(assets.c.asset_id == asset_id).values(**values))
    else:
        conn.execute(assets.insert().values(created_at=now, **values))


def _mark_invalid(conn, asset_id: str) -> None:
    conn.execute(update(assets).where(assets.c.asset_id == asset_id).values(valid=False))


def _finding(conn, repo: RepoRef, item: DiscoveredAsset, code: str, message: str) -> None:
    conn.execute(
        asset_index_findings.insert().values(
            repo=repo.name,
            path=item.directory,
            kind=item.kind,
            name=item.name,
            severity="error",
            code=code,
            message=message,
        )
    )


def index_all(engine: Engine, repo_port, repos: Iterable[RepoRef], **kwargs) -> list[IndexReport]:
    return [index_repository(engine, repo_port, repo, **kwargs) for repo in repos]
