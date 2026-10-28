"""索引流水线：discover → read → parse → validate → relate → persist。

借鉴 Backstage 的处理器链形状；每一步的错误都落 asset_index_findings，界面可见。
"""

from __future__ import annotations

import threading

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine

from fde_asset.core.db import (
    lock_index,
    asset_audiences,
    asset_index_findings,
    asset_relations,
    assets,
    clean_text,
    record_event,
)
from fde_asset.platform.extract import extract_text
from fde_asset.platform.refs.wiki import parse_refs
from fde_asset.platform.repo.ports import RepoRef
from fde_asset.modules.asset.grading import refresh_grades
from fde_asset.modules.asset.relations import auto_link
from fde_asset.platform.cache import recommendation_cache
from fde_asset.modules.asset.manifest import (
    ASSET_ROOT,
    ID_PATTERN,
    KIND_RULES,
    AssetManifest,
    Finding,
    audiences,
    parse_manifest,
    scope_fields,
    validate_asset,
)

NAMESPACE = uuid.UUID("7f9d1c3e-6a25-4a7f-9a9f-9a0e0d1f2b34")


@dataclass
class DiscoveredAsset:
    asset_id: str  # 目录名，也就是资产编号
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


def new_asset_id() -> str:
    return str(uuid.uuid4())


def asset_id_for_candidate(candidate_id: str) -> str:
    """草稿入库后的资产编号。由草稿编号算出来：打回重交还是同一份资产，也不用另存一列。"""
    return str(uuid.uuid5(NAMESPACE, f"candidate|{candidate_id}"))


def legacy_asset_id(
    scope: str, department_code: str, engagement_slug: str, kind: str, name: str
) -> str:
    """旧布局下的资产编号（由层级和名字算出来）。只有导入旧数据时用，好让编号保持不变。"""
    return str(uuid.uuid5(NAMESPACE, f"{scope}|{department_code}|{engagement_slug}|{kind}|{name}"))


def discover(paths: Iterable[str]) -> list[DiscoveredAsset]:
    """`assets/<资产编号>/...` 下每个子目录是一份资产。"""
    prefix = ASSET_ROOT + "/"
    found: dict[str, DiscoveredAsset] = {}
    for path in paths:
        if not path.startswith(prefix):
            continue
        directory, _, relative = path[len(prefix) :].partition("/")
        if not directory or not relative:
            continue
        found.setdefault(directory, DiscoveredAsset(directory, f"{prefix}{directory}"))
        found[directory].files[relative] = path
    return [found[key] for key in sorted(found)]


def _clean(value: object) -> str:
    return "" if value is None else str(value)


#: 建索引是"先查有没有、没有再插"，两处同时扫同一个仓库会互相撞上。
#: 评审通过时会立刻建一次，后台定时任务也会建，所以同一个进程里一次只让一处进行。
_INDEX_LOCK = threading.RLock()


def index_repository(
    engine: Engine,
    repo_port,
    repo: RepoRef,
    *,
    text_limit: int = 200_000,
    file_store: Any = None,
) -> IndexReport:
    """索引仓库里的全部资产，返回统计与错误清单。

    `file_store` 是附件原件的存放处；不给就不提取附件里的文字，其余照常。
    """
    with _INDEX_LOCK:
        return _index_repository(
            engine, repo_port, repo, text_limit=text_limit, file_store=file_store
        )


def _attachments(
    manifest: AssetManifest,
    file_store: Any,
    text_limit: int,
    enriched: dict[str, str] | None = None,
) -> tuple[list[dict], list[str]]:
    """附件清单和从附件里提取出的文字。原件按哈希在文件存储里，不在仓库里。

    enriched：已做完图片理解的附件 {sha256: 带图片说明的文字版}。有就用它建检索，
    截图、架构图里的内容在资产中心也能搜到（2026-10-28）；图片附件、扫描件也靠它才有文字。"""
    enriched = enriched or {}
    listed: list[dict] = []
    chunks: list[str] = []
    for attachment in manifest.spec.attachments:
        entry = {
            "path": f"attachments/{attachment.name}",
            "name": attachment.name,
            "sha256": attachment.sha256,
            "bytes": attachment.bytes,
            "summary": attachment.summary,
            "text_extraction": "skipped",
            "detail": "",
        }
        blob = file_store.get(attachment.sha256) if file_store is not None else None
        if file_store is not None and blob is None:
            entry["text_extraction"] = "missing"
            entry["detail"] = "文件存储里找不到这个附件的原件"
        elif blob is not None and enriched.get(attachment.sha256):
            entry["bytes"] = len(blob)
            entry["text_extraction"] = "ok"
            entry["detail"] = "含图片说明"
            chunks.append(enriched[attachment.sha256][:text_limit])
        elif blob is not None:
            extraction = extract_text(attachment.name, blob, limit=text_limit)
            entry["bytes"] = len(blob)
            entry["text_extraction"] = extraction.status
            entry["detail"] = extraction.detail
            if extraction.status == "ok":
                chunks.append(extraction.text)
        listed.append(entry)
    return listed, chunks


def _index_repository(
    engine: Engine,
    repo_port,
    repo: RepoRef,
    *,
    text_limit: int = 200_000,
    file_store: Any = None,
) -> IndexReport:
    report = IndexReport(repo=repo.name)
    report.head = repo_port.get_head(repo)
    entries = [entry.path for entry in repo_port.list_tree(repo, ASSET_ROOT)]
    discovered = discover(entries)
    trees = repo_port.tree_shas(repo, ASSET_ROOT) if report.head else {}
    seen_ids: set[str] = set()
    #: (负责人, 类型, 标识) → 先占住这个名字的资产编号
    names_taken: dict[tuple[str, str, str], str] = {}
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        lock_index(conn)
        previous = {
            row.asset_id: row
            for row in conn.execute(
                select(assets.c.asset_id, assets.c.tree_sha, assets.c.commit_sha)
            )
        }
        conn.execute(delete(asset_index_findings).where(asset_index_findings.c.repo == repo.name))

        def reject(item: DiscoveredAsset, kind: str, name: str, code: str, message: str) -> None:
            _finding(conn, repo, item, kind, name, code, message)
            report.invalid += 1
            report.findings.append((name or item.asset_id, code))

        for item in discovered:
            if not ID_PATTERN.match(item.asset_id):
                reject(item, "", "", "directory_invalid", "资产目录名必须是资产编号")
                continue
            aid = item.asset_id
            seen_ids.add(aid)

            if "asset.yaml" not in item.files:
                reject(item, "", "", "manifest_missing", "缺少 asset.yaml")
                _mark_invalid(conn, aid)
                continue
            raw_yaml = repo_port.read_file(repo, item.files["asset.yaml"]).decode(
                "utf-8", errors="replace"
            )
            manifest, parse_findings = parse_manifest(raw_yaml)
            if manifest is None:
                for finding in parse_findings:
                    _finding(conn, repo, item, "", "", finding.code, finding.message)
                report.invalid += 1
                report.findings.append(
                    (aid, parse_findings[0].code if parse_findings else "manifest_invalid")
                )
                _mark_invalid(conn, aid)
                continue

            kind, name = manifest.kind, manifest.metadata.name
            if kind not in KIND_RULES:
                reject(item, kind, name, "kind_unsupported", f"不支持的 kind：{kind}")
                _mark_invalid(conn, aid)
                continue
            main_file = KIND_RULES[kind].main_file
            if main_file not in item.files:
                reject(item, kind, name, "main_file_missing", f"缺少主文件 {main_file}")
                _mark_invalid(conn, aid)
                continue
            where = scope_fields(manifest.spec.visibility)
            if where is None:
                reject(
                    item,
                    kind,
                    name,
                    "visibility_invalid",
                    "spec.visibility 至少要写一项：company，"
                    "或 department:/customer:/engagement:/user: 加代号",
                )
                _mark_invalid(conn, aid)
                continue

            main_text = repo_port.read_file(repo, item.files[main_file]).decode(
                "utf-8", errors="replace"
            )
            parsed = validate_asset(manifest, main_text=main_text, scope=where.scope)
            findings: list[Finding] = list(parsed.findings)
            if manifest.metadata.id != aid:
                findings.append(
                    Finding("id_mismatch", f"metadata.id 与目录名不一致（目录是 {aid}）")
                )
            name_key = (manifest.spec.owner, kind, name)
            holder = names_taken.setdefault(name_key, aid)
            if holder != aid:
                findings.append(
                    Finding(
                        "name_duplicate",
                        f"{manifest.spec.owner} 名下已经有一份同类型、同标识的资产（{holder}）",
                    )
                )
            valid = not any(f.severity == "error" for f in findings)

            from fde_asset.modules.asset import attachment_vision

            attachments, extracted_chunks = _attachments(
                manifest,
                file_store,
                text_limit,
                attachment_vision.finished_texts(
                    engine, [item.sha256 for item in manifest.spec.attachments]
                ),
            )

            if not valid:
                for finding in findings:
                    _finding(conn, repo, item, kind, name, finding.code, finding.message)
                report.invalid += 1
                report.findings.append((name, findings[0].code))

            spec_extra = dict(manifest.spec.model_extra or {})
            owner_kind, _, owner_value = manifest.spec.owner.partition(":")
            content_text = "\n".join([main_text, *extracted_chunks])[:text_limit]
            tree_sha = trees.get(aid, "")
            before = previous.get(aid)
            # 目录内容没变就沿用上次记下的提交号：所有资产在一个仓库里，
            # 别人的资产一入库仓库头就变，不能因此把每份资产都算成有了新版本
            unchanged_tree = bool(before is not None and tree_sha and before.tree_sha == tree_sha)
            commit_sha = before.commit_sha if unchanged_tree and before.commit_sha else report.head
            values = dict(
                asset_id=aid,
                scope=where.scope,
                department_code=where.department_code,
                customer_code=where.customer_code,
                engagement_slug=where.engagement_slug,
                repo=repo.name,
                path=item.directory,
                kind=kind,
                name=name,
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
                nature=_clean(manifest.spec.nature) or KIND_RULES[kind].nature,
                version=manifest.spec.version,
                replaced_by=manifest.spec.replacedBy,
                source_json=json.dumps(manifest.spec.source.model_dump(), ensure_ascii=False),
                kind_spec_json=json.dumps(spec_extra, ensure_ascii=False),
                attachments_json=json.dumps(attachments, ensure_ascii=False),
                content_text=content_text,
                commit_sha=commit_sha,
                tree_sha=tree_sha,
                visibility_json=json.dumps(
                    [
                        audience if audience == "company" else f"{audience}:{code}"
                        for audience, code in audiences(manifest.spec.visibility)
                    ],
                    ensure_ascii=False,
                ),
                restricted=bool(manifest.spec.restricted),
                valid=valid,
                updated_at=now,
                deleted_at=None,
            )
            values = {
                key: clean_text(value) if isinstance(value, str) else value
                for key, value in values.items()
            }
            _upsert(conn, aid, values, now)
            if valid:
                report.indexed += 1

            conn.execute(delete(asset_audiences).where(asset_audiences.c.asset_id == aid))
            for audience, code in audiences(manifest.spec.visibility):
                conn.execute(
                    asset_audiences.insert().values(asset_id=aid, audience=audience, code=code)
                )

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
                "head": report.head,
                "indexed": report.indexed,
                "invalid": report.invalid,
                "removed": report.removed,
            },
        )
        if report.invalid:
            record_event(conn, "asset.invalid_found", {"repo": repo.name, "count": report.invalid})
    return report


#: 这两列每次索引都会变，不代表资产内容变了
_BOOKKEEPING = {"updated_at", "commit_sha"}


def _upsert(conn, asset_id: str, values: dict, now: datetime) -> None:
    exists = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
    if exists:
        # 内容没变就不动更新时间：服务每次启动都会重建索引，
        # 否则所有资产的"最近更新"都会变成上一次重启的时间
        unchanged = all(
            getattr(exists, key) == value
            for key, value in values.items()
            if key not in _BOOKKEEPING
        )
        if unchanged:
            values = {key: value for key, value in values.items() if key != "updated_at"}
        conn.execute(update(assets).where(assets.c.asset_id == asset_id).values(**values))
    else:
        conn.execute(assets.insert().values(created_at=now, **values))


def _mark_invalid(conn, asset_id: str) -> None:
    conn.execute(update(assets).where(assets.c.asset_id == asset_id).values(valid=False))


def _finding(
    conn, repo: RepoRef, item: DiscoveredAsset, kind: str, name: str, code: str, message: str
) -> None:
    conn.execute(
        asset_index_findings.insert().values(
            repo=repo.name,
            path=item.directory,
            kind=kind or "",
            name=name or item.asset_id,
            severity="error",
            code=code,
            message=message,
        )
    )


def index_all(
    engine: Engine, repo_port, repos: Iterable[RepoRef], *, owner_of: Any = None, **kwargs
) -> list[IndexReport]:
    reports = [index_repository(engine, repo_port, repo, **kwargs) for repo in repos]
    finish_index(engine, owner_of=owner_of)
    return reports


def finish_index(engine: Engine, *, owner_of: Any = None) -> None:
    """索引之后的收尾：重算等级、重建关系、留版本并发通知。

    只索引了一个仓库（评审通过、下架恢复）之后也要调它，否则新入库的资产没有版本记录，
    订阅的人收不到「新资产」通知。`owner_of(类型, 标识)` 给出项目或 Agent 现在的负责人。
    """
    # 和建索引用同一把锁：收尾也要改 assets 和 asset_relations，和另一处正在建的索引交错就会死锁
    with _INDEX_LOCK:
        # 等级由复用情况决定，不取 asset.yaml 里写的值；入库后统一重算一次
        refresh_grades(engine)
        # 引用在写的时候对方可能还没入库，这里统一解析，并重建自动关系
        auto_link(engine)
        _snapshot_and_notify(engine, owner_of)
    # 资产变了，之前算出来的推荐就不作数了
    recommendation_cache.invalidate()


def _snapshot_and_notify(engine: Engine, owner_of: Any = None) -> None:
    """给每份资产留一份版本快照；第一次出现发「新资产」，内容真的变了发「更新」。"""
    from sqlalchemy import select as _select

    from fde_asset.core.db import assets as _assets
    from fde_asset.modules.notify import service as notify

    with engine.connect() as conn:
        rows = conn.execute(
            _select(_assets).where(_assets.c.deleted_at.is_(None), _assets.c.valid.is_(True))
        ).fetchall()
    for row in rows:
        is_new_version = notify.snapshot_version(engine, row)
        if not is_new_version:
            continue
        if notify.version_count(engine, row.asset_id) == 1:
            # 第一份版本记录 = 刚入库：告诉订阅了它所在行业、类型、标签或负责人的人
            notify.announce_new(engine, row.asset_id)
            continue
        diff = notify.diff_summary(engine, row.asset_id)
        if diff["changed"] or diff["added"] or diff["removed"] or diff["summary_changed"]:
            notify.announce_change(engine, row.asset_id, owner_of=owner_of)
