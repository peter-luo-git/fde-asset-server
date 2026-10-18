"""调整一份资产的可见范围：升级、降级、分享给别的部门、客户、项目或个人。

可见范围写在资产自己的 asset.yaml 里，所以调整就是改这个文件并提交，再重建索引；
文件不搬、资产编号不变，历史、使用记录、订阅都连着。

谁能调：先得是这份资产的负责人或它现在所在范围的评审人；往范围里**加**哪一项，
还得有那一项的评审权（放到全公司要资产评审员，放到某部门要那个部门的评审员或主管，
放到某项目要那个项目的负责人）。点名分享给个人和去掉某一项不另设门槛。

没有那一项评审权时不再拒绝（2026-10-17 起）：能直接改的照改，没权加的那几项各生成一份
可见范围申请，由那一层的评审人审批（`visibility_requests.py`），批准后再加上。
"""

from __future__ import annotations

import json
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets, record_event
from fde_asset.modules.asset.indexer import finish_index, index_repository
from fde_asset.modules.asset.lifecycle import may_manage
from fde_asset.modules.asset.manifest import (
    KIND_RULES,
    VISIBILITY_PATTERN,
    audiences,
    parse_manifest,
    scope_fields,
    validate_asset,
)
from fde_asset.modules.asset.visibility import can_review, visibility_clause
from fde_asset.platform.identity import Principal
from fde_asset.platform.repo.ports import RepoRef
from fde_asset.platform.scan.rules import blocking, scan_files

AUDIENCE_LABEL = {
    "company": "全公司",
    "department": "部门",
    "customer": "客户",
    "engagement": "项目",
    "user": "个人",
}


class SharingError(ValueError):
    pass


class SharingForbidden(SharingError):
    pass


def _entry(audience: str, code: str) -> str:
    return audience if audience == "company" else f"{audience}:{code}"


def _describe(audience: str, code: str) -> str:
    return AUDIENCE_LABEL[audience] + (f" {code}" if code else "")


def normalize(visibility: list[Any]) -> list[str]:
    """校验并整理成标准写法：去重，按范围从大到小排。"""
    items = [str(item).strip() for item in visibility or []]
    for item in items:
        if not VISIBILITY_PATTERN.match(item):
            raise SharingError(
                f"可见范围写法不对：{item}。"
                "应是 company，或 department:/customer:/engagement:/user: 加代号"
            )
    listed = audiences(items)
    if not listed:
        raise SharingError("可见范围至少要留一项，否则谁也看不到这份资产")
    return [_entry(audience, code) for audience, code in listed]


def _may_add(principal: Principal, audience: str, code: str) -> bool:
    if audience == "user":
        return True
    return can_review(
        principal,
        audience,
        department_code=code if audience == "department" else "",
        customer_code=code if audience == "customer" else "",
        engagement_slug=code if audience == "engagement" else "",
    )


def change(
    engine: Engine,
    repo_port: Any,
    principal: Principal,
    asset_id: str,
    visibility: list[Any],
    *,
    note: str = "",
    customer_names: tuple[str, ...] = (),
    sensitive_terms: tuple[str, ...] = (),
    text_limit: int = 200_000,
    owner_of: Any = None,
    file_store: Any = None,
) -> dict[str, Any]:
    from fde_asset.modules.asset import visibility_requests

    wanted = normalize(visibility)
    with engine.connect() as conn:
        row = conn.execute(
            select(assets).where(assets.c.asset_id == asset_id, visibility_clause(principal))
        ).first()
    if row is None:
        raise SharingError("资产不存在或无权查看")
    if not may_manage(principal, row):
        raise SharingForbidden("只有资产负责人或它所在范围的评审人能调整可见范围")

    current = normalize(json.loads(row.visibility_json or "[]")) if row.visibility_json else []
    if wanted == current:
        return {"asset_id": asset_id, "visibility": current, "changed": False, "requested": []}

    added = [pair for pair in audiences(wanted) if _entry(*pair) not in current]
    pending = [_entry(*pair) for pair in added if not _may_add(principal, *pair)]
    requested: list[dict[str, Any]] = []
    if pending:
        requested = visibility_requests.create(
            engine, asset_id=asset_id, entries=pending, requested_by=principal.user_id, note=note
        )
        # 要审批的先不加，其余（去掉的、能直接加的）照常改
        wanted = [entry for entry in wanted if entry not in pending]
        if not wanted or wanted == current:
            return {
                "asset_id": asset_id,
                "visibility": current,
                "changed": False,
                "requested": requested,
            }
        added = [pair for pair in added if _entry(*pair) not in pending]
    result = commit_visibility(
        engine,
        repo_port,
        principal,
        row,
        current=current,
        wanted=wanted,
        added=added,
        note=note,
        customer_names=customer_names,
        sensitive_terms=sensitive_terms,
        text_limit=text_limit,
        owner_of=owner_of,
        file_store=file_store,
    )
    return {**result, "requested": requested}


def approve_request(
    engine: Engine,
    repo_port: Any,
    principal: Principal,
    request_id: str,
    *,
    note: str = "",
    customer_names: tuple[str, ...] = (),
    sensitive_terms: tuple[str, ...] = (),
    text_limit: int = 200_000,
    owner_of: Any = None,
    file_store: Any = None,
) -> dict[str, Any]:
    """批准一份可见范围申请：按资产现在的可见范围加上申请的那一项。"""
    from fde_asset.modules.asset import visibility_requests

    request = visibility_requests.get(engine, request_id)
    if request is None:
        raise SharingError("申请不存在")
    if request.status != "open":
        return {"request_id": request_id, "status": request.status, "idempotent": True}
    if not visibility_requests.may_decide(principal, request.entry):
        raise SharingForbidden(
            f"「{visibility_requests.describe(request.entry)}」要由那一层的评审人审批"
        )
    if request.requested_by == principal.user_id:
        raise SharingForbidden("不能审批自己提的申请，请由其他评审人处理")
    with engine.connect() as conn:
        row = conn.execute(
            select(assets).where(
                assets.c.asset_id == request.asset_id, assets.c.deleted_at.is_(None)
            )
        ).first()
    if row is None:
        raise SharingError("资产已经不在了")
    current = normalize(json.loads(row.visibility_json or "[]")) if row.visibility_json else []
    wanted = normalize([*current, request.entry])
    result: dict[str, Any] = {"asset_id": row.asset_id, "visibility": current, "changed": False}
    if wanted != current:
        result = commit_visibility(
            engine,
            repo_port,
            principal,
            row,
            current=current,
            wanted=wanted,
            added=[visibility_requests.split(request.entry)],
            note=f"批准可见范围申请（{request.requested_by} 提出）" + (f"：{note}" if note else ""),
            customer_names=customer_names,
            sensitive_terms=sensitive_terms,
            text_limit=text_limit,
            owner_of=owner_of,
            file_store=file_store,
        )
    visibility_requests.mark(engine, request_id, status="approved", by=principal.user_id, note=note)
    return {**result, "request_id": request_id, "status": "approved"}


def commit_visibility(
    engine: Engine,
    repo_port: Any,
    principal: Principal,
    row: Any,
    *,
    current: list[str],
    wanted: list[str],
    added: list[tuple[str, str]],
    note: str = "",
    customer_names: tuple[str, ...] = (),
    sensitive_terms: tuple[str, ...] = (),
    text_limit: int = 200_000,
    owner_of: Any = None,
    file_store: Any = None,
) -> dict[str, Any]:
    """把可见范围改成 `wanted`：改 asset.yaml、提交、重建索引。权限由调用方判断。"""
    asset_id = row.asset_id

    repo = RepoRef(name=row.repo)
    manifest_path = f"{row.path}/asset.yaml"
    main_path = f"{row.path}/{KIND_RULES[row.kind].main_file}"
    raw = repo_port.read_file(repo, manifest_path).decode("utf-8")
    main_text = repo_port.read_file(repo, main_path).decode("utf-8", errors="replace")
    document = yaml.safe_load(raw) or {}
    if not isinstance(document, dict) or not isinstance(document.get("spec"), dict):
        raise SharingError("这份资产的 asset.yaml 读不出来，请先修好它")
    document["spec"]["visibility"] = wanted
    updated = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)

    # 范围变大以后要按新范围的规矩再查一遍：改完才发现不合格，这份资产会整个变成无效
    primary = scope_fields(wanted)
    manifest, findings = parse_manifest(updated)
    if manifest is None or primary is None:
        raise SharingError(
            "改完后的 asset.yaml 不合格：" + (findings[0].message if findings else "")
        )
    problems = [
        f
        for f in validate_asset(manifest, main_text=main_text, scope=primary.scope).findings
        if f.severity == "error"
    ]
    if problems:
        raise SharingError("按新的范围，这份资产还不合格：" + problems[0].message)
    if any(audience == "company" for audience, _ in added):
        hits = blocking(
            scan_files(
                {"asset.yaml": updated, KIND_RULES[row.kind].main_file: main_text},
                customer_names=customer_names,
                sensitive_terms=sensitive_terms,
            )
        )
        if hits:
            raise SharingError(
                f"内容里有客户名称或敏感信息（{hits[0].label}），不能放到全公司；请先改掉再升级"
            )

    message = f"chore(asset): 调整「{row.title}」的可见范围" + (f"\n\n{note}" if note else "")
    commit = repo_port.commit_files(
        repo,
        "main",
        {manifest_path: updated.encode("utf-8")},
        message,
        author_name=principal.display_name or principal.user_id,
        author_email=f"{principal.user_id}@fde.local",
    )
    index_repository(engine, repo_port, repo, text_limit=text_limit, file_store=file_store)
    finish_index(engine, owner_of=owner_of)
    with engine.begin() as conn:
        record_event(
            conn,
            "asset.visibility_changed",
            {
                "asset_id": asset_id,
                "from": current,
                "to": wanted,
                "by": principal.user_id,
                "note": note,
                "commit": commit,
            },
        )
    return {"asset_id": asset_id, "visibility": wanted, "changed": True, "commit": commit}
