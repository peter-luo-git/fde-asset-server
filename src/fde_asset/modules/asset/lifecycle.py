"""资产下架、废弃与恢复。

资产的正本在 Git 仓库里，生命周期写在它的 `asset.yaml` 里；所以这里做的是
**替人改那一行并提交**，再重建索引，而不是只改数据库——否则下次索引就被仓库里的旧值盖回去了。

| 操作 | 生命周期 | 效果 |
|---|---|---|
| 下架 | archived | 不再出现在目录、检索和推荐里；知道地址的人还能打开，可以恢复 |
| 废弃 | deprecated | 同上，并且必须指明被哪份资产取代，页面上会引到新的那份 |
| 恢复 | stable / experimental | 重新出现 |
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets, record_event
from fde_asset.modules.asset.indexer import finish_index, index_repository
from fde_asset.modules.asset.visibility import can_review, visibility_clause
from fde_asset.platform.identity import Principal
from fde_asset.platform.refs.wiki import REF_PATTERN

ALLOWED = ("experimental", "stable", "deprecated", "archived")
LABEL = {"experimental": "试用", "stable": "稳定", "deprecated": "废弃", "archived": "下架"}

_LIFECYCLE_LINE = re.compile(r"^(?P<indent>[ \t]+)lifecycle:[ \t]*\S*[ \t]*$", re.M)
_REPLACED_LINE = re.compile(r"^[ \t]+replacedBy:.*\n?", re.M)


class LifecycleError(ValueError):
    pass


class LifecycleForbidden(LifecycleError):
    pass


def may_manage(principal: Principal, row: Any) -> bool:
    """资产的负责人本人，或者有这个作用域评审权的人（项目负责人、部门主管、评审员、管理员）。"""
    if row.owner_kind == "user" and row.owner_value == principal.user_id:
        return True
    return can_review(
        principal,
        row.scope,
        department_code=row.department_code,
        engagement_slug=row.engagement_slug,
        customer_code=row.customer_code,
    )


def rewrite(text: str, lifecycle: str, replaced_by: str) -> str:
    """改 asset.yaml 里的 lifecycle 一行，按需写上或去掉 replacedBy；其余内容一个字不动。"""
    match = _LIFECYCLE_LINE.search(text)
    if match is None:
        raise LifecycleError("这份资产的 asset.yaml 里没有 lifecycle 字段，请到仓库里改")
    indent = match.group("indent")
    without_replaced = _REPLACED_LINE.sub("", text)
    match = _LIFECYCLE_LINE.search(without_replaced)
    assert match is not None
    lines = f"{indent}lifecycle: {lifecycle}"
    if lifecycle == "deprecated":
        lines += f"\n{indent}replacedBy: {replaced_by}"
    return without_replaced[: match.start()] + lines + without_replaced[match.end() :]


def change(
    engine: Engine,
    repo_port: Any,
    repos: list[Any],
    principal: Principal,
    asset_id: str,
    lifecycle: str,
    *,
    replaced_by: str = "",
    note: str = "",
    text_limit: int = 200_000,
    owner_of: Any = None,
) -> dict[str, Any]:
    if lifecycle not in ALLOWED:
        raise LifecycleError(f"生命周期只能是：{'、'.join(ALLOWED)}")
    with engine.connect() as conn:
        row = conn.execute(
            select(assets).where(assets.c.asset_id == asset_id, visibility_clause(principal))
        ).first()
    if row is None:
        raise LifecycleError("资产不存在或无权查看")
    if not may_manage(principal, row):
        raise LifecycleForbidden("只有资产负责人或该作用域的评审人能改它的状态")
    if row.lifecycle == lifecycle:
        return {"asset_id": asset_id, "lifecycle": lifecycle, "changed": False}

    replaced_by = replaced_by.strip().strip("[]")
    if lifecycle == "deprecated":
        if not REF_PATTERN.fullmatch(f"[[{replaced_by}]]"):
            raise LifecycleError(
                "废弃要指明被哪份资产取代，写成 类型/标识，例如 case/import-timeout"
            )
        if replaced_by == f"{row.kind.lower()}/{row.name}":
            raise LifecycleError("不能被自己取代")

    if row.path.endswith(".md"):
        # 规范是单个 Markdown 文件，没有元数据可改
        raise LifecycleError("规范没有生命周期字段；要撤掉一条规范，请在仓库里删除或改写它")
    repo = next((item for item in repos if item.name == row.repo), None)
    if repo is None:
        raise LifecycleError(f"找不到资产所在的仓库：{row.repo}")

    path = f"{row.path}/asset.yaml"
    original = repo_port.read_file(repo, path).decode("utf-8")
    updated = rewrite(original, lifecycle, replaced_by)
    message = f"chore(asset): {LABEL[lifecycle]}「{row.title}」" + (f"\n\n{note}" if note else "")
    commit = repo_port.commit_files(
        repo,
        "main",
        {path: updated.encode("utf-8")},
        message,
        author_name=principal.display_name or principal.user_id,
        author_email=f"{principal.user_id}@fde.local",
    )
    index_repository(engine, repo_port, repo, text_limit=text_limit)
    finish_index(engine, owner_of=owner_of)
    with engine.begin() as conn:
        record_event(
            conn,
            "asset.lifecycle_changed",
            {
                "asset_id": asset_id,
                "from": row.lifecycle,
                "to": lifecycle,
                "replaced_by": replaced_by if lifecycle == "deprecated" else "",
                "by": principal.user_id,
                "note": note,
                "commit": commit,
            },
        )
    return {"asset_id": asset_id, "lifecycle": lifecycle, "changed": True, "commit": commit}
