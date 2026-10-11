"""旧布局 → 新布局的转换。

以前是一层一个仓库（公司、部门、客户、项目各一个），目录按类型分，层级由仓库名决定，
附件直接提交在资产目录里。现在所有资产在同一个仓库的 `assets/<资产编号>/` 下，
可见范围写在 asset.yaml 里，附件原件在文件存储里。

用到这里的只有两处：把旧数据目录搬过来（`migrate_legacy_repos`），以及种子数据
（种子内容还是按旧布局写的，写进仓库前先过一遍这里）。资产编号沿用旧算法算出来的值，
这样数据库里已有的评审、推荐、反馈、订阅记录都还对得上。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Iterable

import yaml

from fde_asset.modules.asset.indexer import legacy_asset_id
from fde_asset.modules.asset.manifest import (
    ASSET_ROOT,
    KIND_RULES,
    NAME_PATTERN,
    split_sections,
    visibility_entry,
)
from fde_asset.platform.repo.ports import RepoRef

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
    ("applications/", "Application"),
)

ENGAGEMENT_LAYOUT: tuple[tuple[str, str], ...] = (
    (".agents/skills/", "Skill"),
    (".fde/assets/sops/", "Sop"),
    (".fde/assets/skills/", "Skill"),
    (".fde/assets/solutions/", "Solution"),
    (".fde/assets/implementations/", "Implementation"),
    (".fde/assets/cases/", "Case"),
    (".fde/assets/experiences/", "Experience"),
    (".fde/assets/applications/", "Application"),
)

ATTACHMENT_PREFIX = "attachments/"


def legacy_repo_ref(name: str) -> RepoRef:
    """旧仓库的名字 → 它承载的层级和归属。"""
    if name == "company-assets":
        return RepoRef(name=name, scope="company")
    if name.startswith("dept-") and name.endswith("-assets"):
        return RepoRef(name=name, scope="department", department_code=name[5:-7])
    if name.startswith("cust-") and name.endswith("-assets"):
        return RepoRef(name=name, scope="customer", customer_code=name[5:-7])
    return RepoRef(name=name, scope="engagement", engagement_slug=name)


def _discover(repo: RepoRef, paths: Iterable[str]) -> dict[tuple[str, str], dict[str, str]]:
    """（类型, 标识）→ {相对资产目录的路径: 仓库内全路径}。"""
    layout = ENGAGEMENT_LAYOUT if repo.scope == "engagement" else COMPANY_LAYOUT
    found: dict[tuple[str, str], dict[str, str]] = {}
    for path in paths:
        for prefix, kind in layout:
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            if kind == "Rule":
                if "/" not in rest and rest.endswith(".md"):
                    found.setdefault((kind, rest[:-3]), {})[KIND_RULES["Rule"].main_file] = path
                break
            name, _, relative = rest.partition("/")
            if name and relative:
                found.setdefault((kind, name), {})[relative] = path
            break
    return found


def _rule_manifest(name: str, text: str) -> dict[str, Any]:
    """旧的规范只有一个 Markdown，没有 asset.yaml；按索引器以前给它填的值补一份。"""
    sections = split_sections(text)
    title = text.splitlines()[0].lstrip("# ").strip() if text.strip() else name
    return {
        "apiVersion": "fde.asset/v1",
        "kind": "Rule",
        "metadata": {
            "name": name,
            "title": (title or name)[:50],
            "summary": sections.get("结论", "").split("\n")[0][:120],
            "tags": [],
        },
        "spec": {"owner": "department:platform", "lifecycle": "stable", "version": "1.0.0"},
    }


def convert_legacy(
    repo: RepoRef, files: dict[str, bytes], file_store: Any
) -> dict[str, dict[str, bytes]]:
    """把一个旧仓库的文件转成新布局。返回 {资产编号: {新仓库里的路径: 内容}}。

    附件原件存进 `file_store`，不出现在返回的文件里。不在旧布局约定目录下的文件不带。
    """
    visibility = visibility_entry(
        repo.scope,
        department_code=repo.department_code,
        customer_code=repo.customer_code,
        engagement_slug=repo.engagement_slug,
    )
    converted: dict[str, dict[str, bytes]] = {}
    for (kind, name), members in sorted(_discover(repo, files).items()):
        asset_id = legacy_asset_id(
            repo.scope, repo.department_code, repo.engagement_slug, kind, name
        )
        prefix = f"{ASSET_ROOT}/{asset_id}"
        target: dict[str, bytes] = {}
        attachments: list[dict[str, Any]] = []
        for relative, full_path in sorted(members.items()):
            content = files[full_path]
            if relative == "asset.yaml":
                continue
            if relative.startswith(ATTACHMENT_PREFIX):
                attachments.append(
                    {
                        "name": relative[len(ATTACHMENT_PREFIX) :].replace("/", "_"),
                        "sha256": file_store.put(content),
                        "bytes": len(content),
                    }
                )
                continue
            target[f"{prefix}/{relative}"] = content

        document: Any = None
        if "asset.yaml" in members:
            raw = files[members["asset.yaml"]]
            try:
                document = yaml.safe_load(raw.decode("utf-8", errors="replace"))
            except yaml.YAMLError:
                document = None
            if not isinstance(document, dict):
                # 解析不了就原样搬过去，索引时会报出来，由人去改
                target[f"{prefix}/asset.yaml"] = raw
                converted[asset_id] = target
                continue
        elif kind == "Rule":
            main = files[members[KIND_RULES["Rule"].main_file]].decode("utf-8", errors="replace")
            safe = name if NAME_PATTERN.match(name) else "rule-" + asset_id[:8]
            document = _rule_manifest(safe, main)
        if document is None:
            converted[asset_id] = target  # 没有 asset.yaml：照搬，索引时会报缺文件
            continue

        metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
        spec = document.get("spec") if isinstance(document.get("spec"), dict) else {}
        document["metadata"] = {"id": asset_id, **{k: v for k, v in metadata.items() if k != "id"}}
        spec["visibility"] = [visibility]
        if attachments:
            spec["attachments"] = attachments
        document["spec"] = spec
        target[f"{prefix}/asset.yaml"] = yaml.safe_dump(
            document, allow_unicode=True, sort_keys=False
        ).encode("utf-8")
        converted[asset_id] = target
    return converted


def migrate_legacy_repos(
    settings: Any, repo_port: Any, file_store: Any, engine: Any, reset: list[str] | None = None
) -> list[str]:
    """把数据目录里按层级分开的旧仓库并进那一个资产仓库。返回搬了哪些旧仓库。

    `reset`：给了就把被退回草稿状态的草稿编号填进去，调用方好去收掉它们的评审待办。

    可以反复执行：搬完的旧仓库会挪到 `repos-legacy/` 留档，下次就没有可搬的了。
    只搬各仓库主分支上的当前内容，历史不带；还在评审中的草稿退回草稿状态，要重新提交。
    """
    from sqlalchemy import update

    from fde_asset.core.db import (
        asset_index_findings,
        asset_reviews,
        assets,
        harvest_candidates,
        record_event,
    )
    from fde_asset.platform.repo.local_git import LocalGitRepo
    from fde_asset.platform.repo.ports import ASSET_REPO_NAME

    repos_dir = Path(settings.repos)
    legacy_paths = sorted(
        path
        for path in repos_dir.glob("*.git")
        if path.is_dir() and path.name != f"{ASSET_REPO_NAME}.git"
    )
    if not legacy_paths:
        return []

    reader = LocalGitRepo(repos_dir)
    target_repo = RepoRef()
    existing = set(repo_port.tree_shas(target_repo, ASSET_ROOT))
    moved: list[str] = []
    for path in legacy_paths:
        legacy = legacy_repo_ref(path.name[:-4])
        files = {
            entry.path: reader.read_file(legacy, entry.path) for entry in reader.list_tree(legacy)
        }
        incoming: dict[str, bytes] = {}
        for asset_id, members in convert_legacy(legacy, files, file_store).items():
            if asset_id not in existing:  # 上次搬到一半又来：已经在新仓库里的不重复写
                incoming.update(members)
                existing.add(asset_id)
        if incoming:
            repo_port.commit_files(
                target_repo, "main", incoming, f"chore(assets): 并入旧仓库 {legacy.name}"
            )
        moved.append(legacy.name)

    archive = Path(settings.root) / "repos-legacy"
    archive.mkdir(parents=True, exist_ok=True)
    for path in legacy_paths:
        destination = archive / path.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(path), str(destination))

    with engine.begin() as conn:
        # 索引记录都指到新仓库上：接下来建索引时，没搬过来的那些会被标成已删除
        conn.execute(update(assets).values(repo=ASSET_REPO_NAME))
        # 旧仓库名下记的索引问题没人再清得掉了（建索引只清自己那个仓库名下的），这里一并清空
        conn.execute(asset_index_findings.delete())
        open_reviews = conn.execute(
            asset_reviews.select().where(asset_reviews.c.status == "open")
        ).fetchall()
        for review in open_reviews:
            conn.execute(
                update(asset_reviews)
                .where(asset_reviews.c.review_id == review.review_id)
                .values(status="rejected", note="资产仓库结构调整，这次提交已失效，请重新提交")
            )
            conn.execute(
                update(harvest_candidates)
                .where(harvest_candidates.c.candidate_id == review.candidate_id)
                .values(status="draft")
            )
            if reset is not None:
                reset.append(review.candidate_id)
        record_event(
            conn,
            "asset.legacy_repos_migrated",
            {"repos": moved, "reviews_reset": len(open_reviews)},
        )
    return moved
