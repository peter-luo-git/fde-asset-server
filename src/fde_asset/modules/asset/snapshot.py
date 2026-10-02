"""会话快照：按「公司 → 部门 → 客户 → 项目」四层合并，并生成 knowledge/INDEX.md（L0）。

越靠后越具体，同名内容后面的覆盖前面的——客户级排在部门之后、项目之前，
因为"这家客户的坑"比部门通用经验具体，又比单个项目的实施记录通用。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets
from fde_asset.modules.asset.manifest import KIND_RULES, SHORT_BY_KIND
from fde_asset.platform.extract import extract_text
from fde_asset.platform.repo.ports import RepoRef

LAYER_DIR = {"Rule": "rules", "Skill": "skills", "Sop": "sops"}
KNOWLEDGE_KINDS = {"Solution", "Implementation", "Case", "Experience"}


@dataclass
class SnapshotResult:
    sha: str
    path: Path
    reused: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    index_bytes: int = 0
    index_truncated: bool = False
    skipped: list[dict[str, Any]] = field(default_factory=list)


def _layer_order(scope: str) -> int:
    return {"company": 0, "department": 1, "customer": 2, "engagement": 3}.get(scope, 4)


def build_snapshot(
    engine: Engine,
    repo_port,
    *,
    snapshot_root: Path,
    department_code: str = "",
    engagement_slug: str = "",
    index_limit: int = 30_000,
    industries: tuple[str, ...] = (),
) -> SnapshotResult:
    """生成或复用快照目录，返回内容寻址的 sha。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(assets).where(
                assets.c.deleted_at.is_(None),
                assets.c.valid.is_(True),
                assets.c.restricted.is_(False),
                assets.c.lifecycle.notin_(["deprecated", "archived", "draft"]),
            )
        ).fetchall()

    selected = [
        row
        for row in rows
        if row.scope == "company"
        or (
            row.scope == "department" and department_code and row.department_code == department_code
        )
        or (
            row.scope == "engagement" and engagement_slug and row.engagement_slug == engagement_slug
        )
    ]
    selected.sort(key=lambda row: (_layer_order(row.scope), row.kind, row.name))

    fingerprint = hashlib.sha256()
    for row in selected:
        fingerprint.update(
            f"{row.scope}|{row.kind}|{row.name}|{row.commit_sha}|{row.updated_at}".encode()
        )
    sha = fingerprint.hexdigest()[:16]
    target = Path(snapshot_root).expanduser() / sha
    if target.exists():
        return SnapshotResult(sha=sha, path=target, reused=True, counts=_count(selected))

    # 同名覆盖：后写的（部门 → 项目）覆盖先写的（公司）
    staging = target.with_suffix(".tmp")
    if staging.exists():
        _rmtree(staging)
    index_rows: dict[tuple[str, str], dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []

    for row in selected:
        repo = RepoRef(
            name=row.repo,
            scope=row.scope,
            department_code=row.department_code or "",
            engagement_slug=row.engagement_slug or "",
        )
        if row.kind in LAYER_DIR:
            sub = LAYER_DIR[row.kind]
            if row.kind == "Rule":
                destination = staging / sub / Path(row.path).name
                _write(destination, repo_port.read_file(repo, row.path))
            else:
                main_file = KIND_RULES[row.kind].main_file
                destination = staging / sub / row.name / main_file
                _write(destination, repo_port.read_file(repo, f"{row.path}/{main_file}"))
                _write(
                    destination.parent / "asset.yaml",
                    repo_port.read_file(repo, f"{row.path}/asset.yaml"),
                )
        elif row.kind in KNOWLEDGE_KINDS:
            destination = staging / "knowledge" / SHORT_BY_KIND[row.kind] / row.name / "README.md"
            _write(destination, repo_port.read_file(repo, f"{row.path}/README.md"))
            for attachment in json.loads(row.attachments_json or "[]"):
                if attachment.get("text_extraction") != "ok":
                    skipped.append(
                        {
                            "asset": row.name,
                            "attachment": attachment.get("path"),
                            "reason": attachment.get("text_extraction"),
                        }
                    )
                    continue
                blob = repo_port.read_file(repo, f"{row.path}/{attachment['path']}")
                text = extract_text(attachment["path"], blob).text
                name = Path(attachment["path"]).name + ".txt"
                _write(destination.parent / "attachments-text" / name, text.encode("utf-8"))
            applicability = json.loads(row.applicability_json or "{}")
            index_rows[(row.kind, row.name)] = {
                "ref": f"[[{SHORT_BY_KIND[row.kind]}/{row.name}]]",
                "kind": KIND_RULES[row.kind].label,
                "title": row.title,
                "summary": row.summary,
                "suitable": applicability.get("suitable", ""),
                "path": f"knowledge/{SHORT_BY_KIND[row.kind]}/{row.name}/README.md",
                "industry": json.loads(row.industry_json or "[]"),
                "scope": row.scope,
            }

    index_text, truncated = _render_index(list(index_rows.values()), index_limit, industries)
    _write(staging / "knowledge" / "INDEX.md", index_text.encode("utf-8"))
    staging.rename(target)
    return SnapshotResult(
        sha=sha,
        path=target,
        reused=False,
        counts=_count(selected),
        index_bytes=len(index_text.encode("utf-8")),
        index_truncated=truncated,
        skipped=skipped,
    )


def _render_index(
    rows: list[dict[str, Any]], limit: int, industries: tuple[str, ...]
) -> tuple[str, bool]:
    def render(entries: list[dict[str, Any]]) -> str:
        lines = [
            "# 公司知识索引（L0）",
            "",
            "> 遇到报错或拿不准的做法，先在这里找条目，再按 path 打开全文。",
            "",
            "| 引用 | 类型 | 标题 | 一句话结论 | 适用 | 路径 |",
            "|---|---|---|---|---|---|",
        ]
        for entry in entries:
            lines.append(
                f"| `{entry['ref']}` | {entry['kind']} | {entry['title']} | {entry['summary']} "
                f"| {entry['suitable']} | `{entry['path']}` |"
            )
        return "\n".join(lines) + "\n"

    text = render(rows)
    if len(text.encode("utf-8")) <= limit or not rows:
        return text, False
    if industries:
        filtered = [r for r in rows if not r["industry"] or set(r["industry"]) & set(industries)]
        text = render(filtered)
        if len(text.encode("utf-8")) <= limit:
            return text + "\n> 已按项目行业裁剪。\n", True
        rows = filtered
    kept = rows[:]
    while kept and len(render(kept).encode("utf-8")) > limit:
        kept.pop()
    return render(kept) + "\n> 已按上限裁剪，完整目录见资产中心。\n", True


def _count(rows: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.kind] = counts.get(row.kind, 0) + 1
    return counts


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
