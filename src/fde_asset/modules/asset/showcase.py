"""资产示范库：FCP 平台预置的优质资产（2026-10-26）。

- 管理员一键安装：走正常的沉淀流程（建草稿 → 校验和扫描 → 提交 → 通过 → 建索引），
  全公司可见，标签「示范资产」，来源记为 fcp-showcase。已经装过的跳过，不重复装；
- 任何看得到的资产都能「复制为草稿」：整份目录（SOP、技能连同脚本和参考文件）复制成新草稿，
  换一个新标识，在上面改成自己的资产。
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets
from fde_asset.modules.asset.manifest import KIND_RULES
from fde_asset.modules.asset.showcase_content import OWNER, SHOWCASE, SHOWCASE_TAG
from fde_asset.modules.harvest import service as harvest

ORIGIN = "fcp-showcase"


def _manifest(item: dict[str, Any]) -> str:
    spec: dict[str, Any] = {
        "owner": OWNER,
        "lifecycle": "stable",
        "industry": ["general"],
        "applicability": {"suitable": item["suitable"], "notSuitable": item["not_suitable"]},
        "version": "1.0.0",
        "source": {"origin": ORIGIN, "note": "FCP 平台预置的示范资产"},
    }
    if item["kind"] == "Sop":
        spec["layer"] = item.get("layer", "L1")
    document = {
        "apiVersion": "fde.asset/v1",
        "kind": item["kind"],
        "metadata": {
            "name": item["name"],
            "title": item["title"],
            "summary": item["summary"],
            "tags": [SHOWCASE_TAG],
        },
        "spec": spec,
    }
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)


def installed(engine: Engine) -> dict[str, Any]:
    """已经入库的示范资产：{标识: 资产行}。"""
    names = [item["name"] for item in SHOWCASE]
    with engine.connect() as conn:
        rows = conn.execute(
            select(assets).where(assets.c.name.in_(names), assets.c.deleted_at.is_(None))
        ).fetchall()
    found = {}
    for row in rows:
        source = json.loads(row.source_json or "{}")
        if source.get("origin") == ORIGIN:
            found[row.name] = row
    return found


def listing(engine: Engine) -> list[dict[str, Any]]:
    present = installed(engine)
    result = []
    for item in SHOWCASE:
        row = present.get(item["name"])
        result.append(
            {
                "name": item["name"],
                "kind": item["kind"],
                "title": item["title"],
                "summary": item["summary"],
                "suitable": item["suitable"],
                "try_it": item.get("try_it", ""),
                "files": sorted(item["files"]),
                "installed": row is not None,
                "asset_id": row.asset_id if row is not None else None,
            }
        )
    return result


def _repo_files(repo_port: Any, row: Any) -> dict[str, str]:
    """已入库示范资产现在的文件（不含 asset.yaml），用来判断内容有没有更新。"""
    from fde_asset.platform.repo.ports import RepoRef

    repo = RepoRef(name=row.repo)
    files: dict[str, str] = {}
    for entry in repo_port.list_tree(repo, row.path):
        relative = entry.path[len(row.path) + 1 :]
        if not relative or relative == "asset.yaml":
            continue
        try:
            files[relative] = repo_port.read_file(repo, entry.path).decode("utf-8")
        except UnicodeDecodeError:
            continue
    return files


def install(
    engine: Engine,
    principal: Any,
    repo_port: Any,
    *,
    target_repo: Any,
    scan_context: Any = None,
) -> dict[str, Any]:
    """把示范资产装进去：没装的新装；装过但平台更新了内容的，按修订入库（编号不变、版本加一，
    项目里已经关联、设成默认流程的不受影响）；内容一样的跳过。返回装了、更新了、跳过、失败（附原因）。"""
    present = installed(engine)
    done, updated, skipped, failed = [], [], [], []
    for item in SHOWCASE:
        row = present.get(item["name"])
        if row is not None and _repo_files(repo_port, row) == item["files"]:
            skipped.append(item["name"])
            continue
        files = {**item["files"], "asset.yaml": _manifest(item)}
        try:
            draft = harvest.create_draft(
                engine,
                principal,
                harvest.CandidateInput(
                    kind=item["kind"],
                    name=item["name"],
                    title=item["title"],
                    scope="company",
                    origin=ORIGIN,
                    source={"origin": ORIGIN},
                    files=files,
                    revises_asset_id=row.asset_id if row is not None else "",
                ),
            )
            submitted = harvest.submit(
                engine,
                principal,
                repo_port,
                draft["candidate_id"],
                target_repo=target_repo,
                scan_context=scan_context,
                medium_risk_confirmed=True,
            )
            # 平台预置的资产由管理员安装，不另走人工评审
            harvest.decide(
                engine,
                principal,
                repo_port,
                submitted["review_id"],
                approve=True,
                target_repo=target_repo,
                note="FCP 平台预置的示范资产，由管理员安装",
            )
            (updated if row is not None else done).append(item["name"])
        except harvest.HarvestError as exc:
            failed.append({"name": item["name"], "reason": str(exc)})
    return {"installed": done, "updated": updated, "skipped": skipped, "failed": failed}


# ---------- 复制为草稿 ----------

NAME_SAFE = re.compile(r"[^a-z0-9-]+")


def _copy_name(original: str) -> str:
    base = NAME_SAFE.sub("-", original.lower()).strip("-")[:50] or "copy"
    return f"{base}-copy-{secrets.token_hex(2)}"


def copy_as_draft(
    engine: Engine,
    principal: Any,
    repo_port: Any,
    row: Any,
    *,
    scope: str,
    department_code: str = "",
    engagement_slug: str = "",
    customer_code: str = "",
    title: str = "",
) -> dict[str, Any]:
    """把一份资产整目录复制成新草稿：换新标识，标题加「（副本）」，来源记成从哪份复制的。"""
    from fde_asset.modules.asset import skill_package
    from fde_asset.platform.repo.ports import RepoRef

    repo = RepoRef(name=row.repo)
    files: dict[str, str] = {}
    for entry in repo_port.list_tree(repo, row.path):
        relative = entry.path[len(row.path) + 1 :]
        if relative.startswith("attachments/"):
            continue
        try:
            files[relative] = repo_port.read_file(repo, entry.path).decode("utf-8")
        except UnicodeDecodeError:
            continue  # 二进制文件不进草稿（草稿只收文本）
    if row.kind == "Sop" and scope == "user":
        raise harvest.HarvestError("个人专用只支持技能；SOP 请复制到项目或部门")
    name = _copy_name(row.name)
    new_title = (title or f"{row.title}（副本）")[:50]
    document = yaml.safe_load(files.get("asset.yaml") or "") or {}
    metadata = document.setdefault("metadata", {})
    spec = document.setdefault("spec", {})
    metadata.pop("id", None)
    metadata["name"] = name
    metadata["title"] = new_title
    metadata["tags"] = [tag for tag in (metadata.get("tags") or []) if tag != SHOWCASE_TAG]
    for key in ("visibility", "attachments", "executables", "replacedBy"):
        spec.pop(key, None)
    spec["owner"] = (
        f"user:{principal.user_id}"
        if scope == "user" or not principal.department_code
        else f"department:{principal.department_code}"
    )
    spec["lifecycle"] = "experimental"
    spec["version"] = "0.1.0"
    spec["source"] = {"origin": "copy", "note": f"复制自 {row.title}（{row.asset_id}）"}
    files["asset.yaml"] = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    main_file = KIND_RULES[row.kind].main_file
    if row.kind == "Skill" and main_file in files:
        # 技能的 name 要和标识一致，否则 Agent 认不出
        files[main_file] = re.sub(r"(?m)^name:\s*.*$", f"name: {name}", files[main_file], count=1)
        if skill_package.parse_frontmatter(files[main_file]) is None:
            files[main_file] = skill_package.with_frontmatter(
                files[main_file], name=name, description=row.summary or ""
            )
    return harvest.create_draft(
        engine,
        principal,
        harvest.CandidateInput(
            kind=row.kind,
            name=name,
            title=new_title,
            scope=scope,
            department_code=department_code or principal.department_code,
            engagement_slug=engagement_slug,
            customer_code=customer_code,
            origin="copy",
            source={"origin": "copy", "copiedFrom": row.asset_id},
            files=files,
        ),
    )
