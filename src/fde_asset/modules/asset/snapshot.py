"""会话快照：按「公司 → 部门 → 客户 → 项目」四层合并，并生成 knowledge/INDEX.md（L0）。

越靠后越具体，同名内容后面的覆盖前面的——客户级排在部门之后、项目之前，
因为"这家客户的坑"比部门通用经验具体，又比单个项目的实施记录通用。

带哪些资产分两种情况（`selection`）：

- **linked**：给了目标（项目或 Agent）。上层（公司、部门、客户）的资产只带两种：
  **设成必选的**（见 `mandatory.py`），和**关联给这个项目或 Agent 的**；其余一份都不带，
  不拿整层来兜底（2026-10-16 起）。规范和流程也一样，不再按类型自动必带（2026-10-17 起）。
  带了某个流程，就把它 extends 的上层流程一起带上，分层继承才接得上。
  项目自己沉淀的全带（那本来就是它自己的东西）。
- **scope**：没给目标，只是看一个作用域里有什么（不是给哪个 Agent 的），作用域内的全列出来。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets, target_assets
from fde_asset.modules.asset import mandatory as mandatory_module
from fde_asset.modules.asset.manifest import KIND_RULES, SHORT_BY_KIND, owner_slug
from fde_asset.modules.asset.visibility import audience_clause
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
    #: linked = 给了目标，按它关联的资产挑；scope = 没给目标，作用域内全列出来
    selection: str = "scope"
    #: 快照里有几份是因为被关联才带上的
    linked: int = 0


def _layer_order(scope: str) -> int:
    return {"company": 0, "department": 1, "customer": 2, "engagement": 3}.get(scope, 4)


@dataclass
class Selection:
    """给某个项目或 Agent 带哪些资产，以及每份在 Agent 那里叫什么。"""

    rows: list[Any]
    #: {资产编号: 挂载名}，同类型内不重名
    mounted: dict[str, str]
    #: linked = 给了目标，按它关联的资产挑；scope = 没给目标，可见范围内全列出来
    selection: str
    linked: int
    not_carried: list[dict[str, Any]]
    #: 有几份是因为设成必选才带上的
    mandatory: int = 0


def select_assets(
    engine: Engine,
    *,
    department_code: str = "",
    engagement_slug: str = "",
    customer_code: str = "",
    target_type: str = "",
    target_id: str = "",
    also_targets: tuple[tuple[str, str], ...] = (),
    username: str = "",
    skill_names: tuple[str, ...] = (),
) -> Selection:
    """`also_targets`：还要一并算上谁的关联，例如这次干活的 Agent。

    关联时定下的挂载名以第一个目标（项目）的为准，后面的目标只补它没有的。
    `username`：只在这个人和 Agent 私聊时给，带上他的个人必选资产（以及点名给他、他能看的）。
    """
    with engine.connect() as conn:
        rows = conn.execute(
            select(assets).where(
                assets.c.deleted_at.is_(None),
                assets.c.valid.is_(True),
                assets.c.restricted.is_(False),
                assets.c.lifecycle.notin_(["deprecated", "archived", "draft"]),
                # 全公司的，加上可见范围里点到这个部门、客户、项目的
                audience_clause(
                    department_codes=[department_code],
                    customer_codes=[customer_code],
                    engagement_slugs=[engagement_slug],
                    user_id=username,
                ),
            )
        ).fetchall()
        mandatory_ids = mandatory_module.ids_for(
            conn,
            department_code=department_code,
            customer_code=customer_code,
            engagement_slug=engagement_slug,
            username=username,
        )
        linked_ids: set[str] = set()
        linked_titles: dict[str, str] = {}
        #: 关联时定下的挂载名
        recorded_names: dict[str, str] = {}
        targets = [(target_type, target_id), *also_targets]
        for one_type, one_id in targets:
            if not one_type or not one_id:
                continue
            for row in conn.execute(
                select(target_assets.c.asset_id, target_assets.c.mount_name)
                .where(
                    target_assets.c.target_type == one_type,
                    target_assets.c.target_id == one_id,
                )
                .order_by(target_assets.c.id)
            ):
                linked_ids.add(row.asset_id)
                if row.mount_name:
                    recorded_names.setdefault(row.asset_id, row.mount_name)
        if linked_ids:
            linked_titles = {
                row.asset_id: row.title
                for row in conn.execute(
                    select(assets.c.asset_id, assets.c.title).where(
                        assets.c.asset_id.in_(linked_ids)
                    )
                )
            }

    in_scope = list(rows)
    in_scope.sort(key=lambda row: (_layer_order(row.scope), row.kind, row.name, row.asset_id))

    # 给了目标：必选的、关联的、项目自己的才带；没给目标才是看整个作用域
    available = {row.asset_id for row in in_scope}
    picked = linked_ids & available
    forced = mandatory_ids & available
    selection = "linked" if any(one_type and one_id for one_type, one_id in targets) else "scope"
    # SOP 只用于按步骤执行任务（当前步骤由平台注入），不进资产包当参考（2026-10-25）
    wanted = {
        row.asset_id
        for row in in_scope
        if row.kind != "Sop"
        and (
            selection == "scope"
            or row.scope == "engagement"
            or row.asset_id in picked
            or row.asset_id in forced
        )
    }
    # 个人专用的技能：只在本人和 Agent 的私聊里带（和个人必选一样，username 只在私聊里给）
    if username:
        wanted |= {
            row.asset_id
            for row in in_scope
            if row.kind == "Skill" and row.scope == "user" and row.owner_ref == f"user:{username}"
        }
    # 当前流程步骤点名要用的技能（SOP「技能：」），没关联也带上；同名的优先项目自己的
    for name in skill_names:
        same = [row for row in in_scope if row.kind == "Skill" and row.name == name]
        same.sort(key=lambda row: (row.scope != "engagement", row.scope != "user", row.asset_id))
        if same:
            wanted.add(same[0].asset_id)
    selected = [row for row in in_scope if row.asset_id in wanted]
    sop_ids = {row.asset_id for row in in_scope if row.kind == "Sop"}
    # 关联了、设了必选却带不进来的要说出来（以前关联的 SOP 不算：SOP 本来就不进资产包）：否则负责人以为 Agent 拿到了，其实没有
    not_carried = [
        {
            "asset": linked_titles.get(asset_id, asset_id),
            "asset_id": asset_id,
            "reason": "linked_but_not_available",
        }
        for asset_id in sorted(linked_ids - wanted - sop_ids)
    ]

    return Selection(
        rows=selected,
        mounted=_mount_names(selected, recorded_names),
        selection=selection,
        linked=len(picked),
        not_carried=not_carried,
        mandatory=len(forced - picked),
    )


def build_snapshot(
    engine: Engine,
    repo_port,
    *,
    snapshot_root: Path,
    department_code: str = "",
    engagement_slug: str = "",
    index_limit: int = 30_000,
    industries: tuple[str, ...] = (),
    customer_code: str = "",
    target_type: str = "",
    target_id: str = "",
    file_store: Any = None,
) -> SnapshotResult:
    """生成或复用快照目录，返回内容寻址的 sha。

    `file_store` 是附件原件的存放处；不给就不带附件里的文字。
    """
    chosen = select_assets(
        engine,
        department_code=department_code,
        engagement_slug=engagement_slug,
        customer_code=customer_code,
        target_type=target_type,
        target_id=target_id,
    )
    selected, mounted, selection = chosen.rows, chosen.mounted, chosen.selection
    not_carried = chosen.not_carried

    fingerprint = hashlib.sha256()
    fingerprint.update(f"selection={selection}|".encode())
    for row in selected:
        fingerprint.update(
            f"{row.asset_id}|{row.kind}|{mounted[row.asset_id]}|{row.commit_sha}|{row.updated_at}".encode()
        )
    sha = fingerprint.hexdigest()[:16]
    target = Path(snapshot_root).expanduser() / sha
    if target.exists():
        return SnapshotResult(
            sha=sha,
            path=target,
            reused=True,
            counts=_count(selected),
            skipped=not_carried,
            selection=selection,
            linked=chosen.linked,
        )

    staging = target.with_suffix(".tmp")
    if staging.exists():
        _rmtree(staging)
    index_rows: dict[tuple[str, str], dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = list(not_carried)

    carried_manifest: list[dict[str, Any]] = []
    for row in selected:
        repo = RepoRef(name=row.repo)
        mount = mounted[row.asset_id]
        carried_manifest.append(
            {
                "asset_id": row.asset_id,
                "kind": row.kind,
                "name": row.name,
                "mount_name": mount,
                "owner": row.owner_ref,
                "version": row.version,
                "commit_sha": row.commit_sha,
            }
        )
        if row.kind in LAYER_DIR:
            sub = LAYER_DIR[row.kind]
            if row.kind == "Rule":
                # 规范在快照里仍是 rules/<名字>.md 一个文件，Agent 那边按这个形状读
                destination = staging / sub / f"{mount}.md"
                main_file = KIND_RULES["Rule"].main_file
                _write(destination, repo_port.read_file(repo, f"{row.path}/{main_file}"))
            else:
                main_file = KIND_RULES[row.kind].main_file
                destination = staging / sub / mount / main_file
                content = repo_port.read_file(repo, f"{row.path}/{main_file}")
                if mount != row.name:
                    content = _renamed(content, row.name, mount)
                _write(destination, content)
                _write(
                    destination.parent / "asset.yaml",
                    repo_port.read_file(repo, f"{row.path}/asset.yaml"),
                )
        elif row.kind in KNOWLEDGE_KINDS:
            destination = staging / "knowledge" / SHORT_BY_KIND[row.kind] / mount / "README.md"
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
                blob = (
                    file_store.get(attachment.get("sha256", "")) if file_store is not None else None
                )
                if blob is None:
                    skipped.append(
                        {
                            "asset": row.name,
                            "attachment": attachment.get("path"),
                            "reason": "missing",
                        }
                    )
                    continue
                text = extract_text(attachment["path"], blob).text
                name = Path(attachment["path"]).name + ".txt"
                _write(destination.parent / "attachments-text" / name, text.encode("utf-8"))
            applicability = json.loads(row.applicability_json or "{}")
            index_rows[(row.kind, mount)] = {
                "ref": f"[[{SHORT_BY_KIND[row.kind]}/{row.name}]]",
                "kind": KIND_RULES[row.kind].label,
                "title": row.title,
                "summary": row.summary,
                "suitable": applicability.get("suitable", ""),
                "path": f"knowledge/{SHORT_BY_KIND[row.kind]}/{mount}/README.md",
                "industry": json.loads(row.industry_json or "[]"),
                "scope": row.scope,
            }

    index_text, truncated = _render_index(list(index_rows.values()), index_limit, industries)
    _write(staging / "knowledge" / "INDEX.md", index_text.encode("utf-8"))
    # 快照里每个名字对应哪份资产的哪个版本：用了什么要按编号报回来，名字可能是加过负责人的
    _write(
        staging / ".fde-assets.json",
        json.dumps({"assets": carried_manifest}, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    staging.rename(target)
    return SnapshotResult(
        sha=sha,
        path=target,
        reused=False,
        counts=_count(selected),
        index_bytes=len(index_text.encode("utf-8")),
        index_truncated=truncated,
        skipped=skipped,
        selection=selection,
        linked=chosen.linked,
    )


def _mount_names(selected: list[Any], recorded: dict[str, str]) -> dict[str, str]:
    """快照里每份资产叫什么：{资产编号: 名字}，同类型内不重名。

    关联时定下的名字优先占位；其余的用自己的标识，撞了名就加上负责人
    （规范、流程这些不靠关联、整层都带的资产，也可能来自不同负责人而同名）。
    """
    names: dict[str, str] = {}
    used: dict[str, set[str]] = {}
    for row in selected:
        name = recorded.get(row.asset_id)
        if name and name not in used.setdefault(row.kind, set()):
            names[row.asset_id] = name
            used[row.kind].add(name)
    for row in selected:
        if row.asset_id in names:
            continue
        taken = used.setdefault(row.kind, set())
        name = row.name
        if name in taken:
            name = f"{row.name}-{owner_slug(row.owner_value)}"
        if name in taken:
            name = f"{name}-{row.asset_id[:8]}"
        names[row.asset_id] = name
        taken.add(name)
    return names


_FRONT_MATTER_NAME = re.compile(r"\A(---\n(?:.*\n)*?)name:[ \t]*\S+[ \t]*\n", re.M)


def _renamed(content: bytes, original: str, mount: str) -> bytes:
    """技能文件开头写着自己的名字，Agent 按它认技能；目录改了名，这里也要跟着改。"""
    text = content.decode("utf-8", errors="replace")
    match = _FRONT_MATTER_NAME.match(text)
    if match is None or f"name: {original}" not in match.group(0):
        return content
    return (match.group(1) + f"name: {mount}\n" + text[match.end() :]).encode("utf-8")


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
