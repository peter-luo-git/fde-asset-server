"""沉淀候选（2026-10-28）：会话里可能值得沉淀成资产的内容，先统一记下来，在项目「资产 → 待沉淀」里集中处理。

来源：
- Agent 主动提：平台工具 fde_asset_harvest_suggest 经内部接口记进来（可复用的方案、踩过的坑、做好的模板，
  以及临时装过、该加进技能的依赖）；
- 人在消息上「记为可沉淀」。
会话目录里 Agent 做出的文件不自动记，在待沉淀页按需勾选。

可见范围：频道里的候选项目成员都能看，可写成员能处理；私聊里的只有本人能看、能处理。

处理方式（status）：
- open 待处理（计入红色数字）；hold 待定（不计入，留在列表里）；
- drafted 已起草：跟着草稿走——评审中、已沉淀（入库）；草稿删掉了自动回到待处理；
- linked 已有资产覆盖；duplicate 和另一条是同一件事（合并过去）；ignored 不沉淀（可写原因）。
不再冒出来：新候选先在本项目里按「类型 + 标题」找我能看到的同一件事——
还没处理（待处理、待定）的合并进去；处理过的不新建，只在原记录上记「又被提到 1 次」。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Connection, Engine

from fde_asset.core.db import clean_text, harvest_candidates, harvest_suggestions, record_event
from fde_asset.modules.asset.indexer import asset_id_for_candidate
from fde_asset.platform.identity import (
    HarvestSourceNotFound,
    PlatformDirectory,
    PlatformUnavailable,
    Principal,
    PrincipalNotFound,
)

SCOPES = {"channel", "dm"}
SOURCES = {"agent", "message"}
#: 建议的类型：资产类型，或者 skill_deps（该加进技能的依赖）
KINDS = {
    "Solution",
    "Implementation",
    "Case",
    "Experience",
    "Rule",
    "Skill",
    "Sop",
    "skill_deps",
}
STATUSES = {"open", "hold", "drafted", "linked", "duplicate", "ignored"}
#: 还没处理的：新来的同一件事合并进去
UNSETTLED = {"open", "hold"}
MAX_FILES = 20
MAX_MENTIONS = 20


class SuggestionError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def current_principal(directory, principal: Principal) -> Principal:
    """写入不使用登录会话中缓存的旧成员角色。"""
    if not isinstance(directory, PlatformDirectory):
        return principal
    try:
        directory.refresh()
        return directory.resolve(principal.user_id)
    except PrincipalNotFound:
        raise SuggestionError("用户不存在或已失效", 401) from None
    except PlatformUnavailable:
        raise SuggestionError("暂时无法向平台确认身份", 503) from None


def checked_source(directory, principal: Principal, payload: dict[str, Any], *, message: bool):
    """来源由平台按真实消息/会话 ACL 核准；写权限由调用方按当前项目成员关系另行检查。"""
    values = {
        key: str(payload.get(key) or "")
        for key in ("engagement_slug", "scope", "scope_id", "conversation_id", "message_id")
    }
    if (
        not values["engagement_slug"]
        or len(values["engagement_slug"]) > 128
        or values["scope"] not in SCOPES
        or not values["scope_id"]
        or any(len(values[key]) > 64 for key in ("scope_id", "conversation_id", "message_id"))
        or (message and not values["message_id"])
        or (not message and not values["conversation_id"])
    ):
        raise SuggestionError("要给出有效的项目、会话范围和真实消息或 Agent 会话编号")
    try:
        return directory.harvest_source_access(username=principal.user_id, **values)
    except HarvestSourceNotFound:
        raise SuggestionError("消息或会话不存在或无权访问", 404) from None
    except PlatformUnavailable:
        raise SuggestionError("暂时无法向平台确认沉淀来源", 503) from None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, limit: int) -> str:
    return clean_text(str(value or "").strip())[:limit]


def _files(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value[:MAX_FILES]:
        if not isinstance(item, str):
            continue
        path = item.strip()
        if (
            path
            and not path.startswith("/")
            and "\\" not in path
            and "\x00" not in path
            and not any(part.lower() in {"", ".", "..", ".git"} for part in path.split("/"))
            and len(path) <= 512
            and path not in cleaned
        ):
            cleaned.append(path)
    return cleaned


def _same_title(title: str) -> str:
    """比较标题时忽略大小写和空格（和库里 lower(replace(title, ' ', '')) 一致）。"""
    return title.lower().replace(" ", "")


def may_see(principal: Principal, row: Any) -> bool:
    if row.scope == "dm":
        return row.owner_user == principal.user_id
    return principal.is_admin or row.engagement_slug in principal.engagement_slugs


def may_handle(principal: Principal, row: Any) -> bool:
    """私聊的本人处理；频道的项目可写成员（负责人、成员）和管理员处理，只读成员只能看。"""
    if row.scope == "dm":
        return row.owner_user == principal.user_id and may_write_project(
            principal, row.engagement_slug
        )
    return may_write_project(principal, row.engagement_slug)


def may_write_project(principal: Principal, engagement_slug: str) -> bool:
    """每次写入都按当前项目成员关系判断；只读成员和已撤权成员不可写。"""
    if principal.is_admin:
        return True
    return any(
        m.engagement_slug == engagement_slug and m.role in {"owner", "member"}
        for m in principal.memberships
    )


def _visible_clause(principal: Principal, engagement_slug: str | None = None):
    """我能看到的：我参与项目的频道里的（管理员看全部频道），加上我自己的私聊里的。"""
    channel = harvest_suggestions.c.scope == "channel"
    if not principal.is_admin:
        channel = and_(
            channel, harvest_suggestions.c.engagement_slug.in_(sorted(principal.engagement_slugs))
        )
    mine = and_(
        harvest_suggestions.c.scope == "dm",
        harvest_suggestions.c.owner_user == principal.user_id,
    )
    clause = or_(channel, mine)
    if engagement_slug:
        clause = and_(clause, harvest_suggestions.c.engagement_slug == engagement_slug)
    return clause


def _draft_states(conn: Connection, candidate_ids: set[str]) -> dict[str, dict[str, str]]:
    """起草过的跟着草稿走：评审中、已沉淀（链到入库的资产）、被退回。查不到的就是草稿删掉了。"""
    if not candidate_ids:
        return {}
    rows = conn.execute(
        select(
            harvest_candidates.c.candidate_id,
            harvest_candidates.c.status,
            harvest_candidates.c.revises_asset_id,
        ).where(harvest_candidates.c.candidate_id.in_(sorted(candidate_ids)))
    ).fetchall()
    states: dict[str, dict[str, str]] = {}
    for row in rows:
        label = {
            "draft": "草稿中",
            "submitted": "评审中",
            "merged": "已沉淀",
            "rejected": "评审退回",
        }.get(row.status, row.status)
        states[row.candidate_id] = {
            "draft_status": row.status,
            "draft_label": label,
            "asset_id": (row.revises_asset_id or asset_id_for_candidate(row.candidate_id))
            if row.status == "merged"
            else "",
        }
    return states


def public(
    row: Any, draft: dict[str, str] | None = None, principal: Principal | None = None
) -> dict[str, Any]:
    return {
        "id": row.id,
        "engagement_slug": row.engagement_slug,
        "scope": row.scope,
        "scope_id": row.scope_id,
        "conversation_id": row.conversation_id,
        "source": row.source,
        "kind": row.kind,
        "title": row.title,
        "summary": row.summary,
        "message_id": row.message_id,
        "files": json.loads(row.files_json or "[]"),
        "extra": json.loads(row.extra_json or "{}"),
        "status": row.status,
        "candidate_id": row.candidate_id,
        # 已起草的：草稿现在走到哪一步了
        **(draft or {}),
        "decision_note": row.decision_note or "",
        "related_asset_id": row.related_asset_id or "",
        "merged_into": row.merged_into or "",
        "mentions": row.mentions or 0,
        "mentions_list": json.loads(row.mentions_json or "[]"),
        "decided_by": row.decided_by or "",
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "can_handle": may_handle(principal, row) if principal is not None else False,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def record(
    engine: Engine,
    *,
    engagement_slug: str,
    scope: str,
    scope_id: str,
    owner_user: str,
    source: str,
    created_by: str,
    kind: str = "",
    title: str = "",
    summary: str = "",
    conversation_id: str = "",
    message_id: str = "",
    files: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """记一条候选。

    先在本项目里按「类型 + 标题」找同一件事（只找提出人看得到的：项目频道里的，和他自己私聊里的）：
    - 还没处理（待处理、待定）：合并进去（同一会话的文件取并集、说明用新的）；
    - 已经处理过（起草、已有、重复、不沉淀）：不新建，在原记录上记「又被提到 1 次」，返回 already_handled。
    """
    if scope not in SCOPES or not scope_id or not engagement_slug:
        raise SuggestionError("要给出项目、会话范围（channel 或 dm）和会话编号")
    if source not in SOURCES:
        raise SuggestionError("来源只能是 agent 或 message")
    if kind and kind not in KINDS:
        raise SuggestionError(f"不认识的资产类型：{kind}")
    title = _text(title, 200)
    if not title:
        raise SuggestionError("要有一个标题")
    files_list = _files(files)
    now = _now()
    with engine.begin() as conn:
        # 同一件事：本项目里同类型，标题忽略大小写和空格后相同；私聊的只找自己的
        same = conn.execute(
            select(harvest_suggestions)
            .where(
                harvest_suggestions.c.engagement_slug == engagement_slug,
                harvest_suggestions.c.kind == kind,
                func.lower(func.replace(harvest_suggestions.c.title, " ", ""))
                == _same_title(title),
                harvest_suggestions.c.scope == scope,
                *(
                    [
                        harvest_suggestions.c.owner_user == owner_user,
                        harvest_suggestions.c.scope_id == scope_id,
                    ]
                    if scope == "dm"
                    else []
                ),
            )
            .order_by(harvest_suggestions.c.created_at)
        ).fetchall()
        # 重复的指向它合并到的那条
        by_id = {row.id: row for row in same}
        existing = None
        for row in same:
            target = by_id.get(row.merged_into, row) if row.status == "duplicate" else row
            # 私聊里提的同一件事，只在同一私聊里合并内容；频道的可以合并别的频道的
            if (
                target.status in UNSETTLED
                and target.scope == scope
                and (scope == "channel" or target.scope_id == scope_id)
            ):
                existing = target
                break
            if target.status not in UNSETTLED and existing is None:
                existing = target
        if existing is not None and existing.status in UNSETTLED:
            same_origin = (
                existing.scope_id == scope_id and existing.conversation_id == conversation_id
            )
            # files 只描述原会话目录，不能把另一会话的相对路径挂到原会话下。
            merged = list(
                dict.fromkeys(
                    [
                        *json.loads(existing.files_json or "[]"),
                        *(files_list if same_origin else []),
                    ]
                )
            )
            merged_extra = {**json.loads(existing.extra_json or "{}"), **(extra or {})}
            if "packages" in (extra or {}):
                merged_extra["packages"] = sorted(
                    set(json.loads(existing.extra_json or "{}").get("packages") or [])
                    | set((extra or {}).get("packages") or [])
                )
            conn.execute(
                update(harvest_suggestions)
                .where(harvest_suggestions.c.id == existing.id)
                .values(
                    summary=_text(summary, 4000) or existing.summary,
                    files_json=json.dumps(merged[:MAX_FILES], ensure_ascii=False),
                    extra_json=json.dumps(merged_extra, ensure_ascii=False),
                    conversation_id=existing.conversation_id,
                    message_id=(message_id or existing.message_id)
                    if same_origin
                    else existing.message_id,
                    updated_at=now,
                )
            )
            suggestion_id = existing.id
            outcome = "merged"
        elif existing is not None:
            # 处理过的：只记一次「又被提到」，不再进待处理
            mentions = json.loads(existing.mentions_json or "[]")
            mentions.append(
                {
                    "source": source,
                    "by": created_by,
                    "scope": scope,
                    "at": now.isoformat(),
                    "message_id": message_id,
                }
            )
            conn.execute(
                update(harvest_suggestions)
                .where(harvest_suggestions.c.id == existing.id)
                .values(
                    mentions=(existing.mentions or 0) + 1,
                    mentions_json=json.dumps(mentions[-MAX_MENTIONS:], ensure_ascii=False),
                    updated_at=now,
                )
            )
            suggestion_id = existing.id
            outcome = "already_handled"
        else:
            suggestion_id = uuid.uuid4().hex
            conn.execute(
                harvest_suggestions.insert().values(
                    id=suggestion_id,
                    engagement_slug=engagement_slug,
                    scope=scope,
                    scope_id=scope_id,
                    conversation_id=conversation_id,
                    owner_user=owner_user,
                    source=source,
                    kind=kind,
                    title=title,
                    summary=_text(summary, 4000),
                    message_id=message_id,
                    files_json=json.dumps(files_list, ensure_ascii=False),
                    extra_json=json.dumps(extra or {}, ensure_ascii=False),
                    status="open",
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
            )
            record_event(
                conn,
                "harvest.suggested",
                {
                    "id": suggestion_id,
                    "engagement_slug": engagement_slug,
                    "scope": scope,
                    "source": source,
                    "kind": kind,
                    "created_by": created_by,
                },
            )
            outcome = "recorded"
        row = conn.execute(
            select(harvest_suggestions).where(harvest_suggestions.c.id == suggestion_id)
        ).first()
    return {**public(row), "outcome": outcome}


def _settle_drafts(
    conn: Connection, rows: list[Any]
) -> tuple[list[Any], dict[str, dict[str, str]]]:
    """起草过的看草稿现状；草稿被删掉了（查不到）的自动回到待处理。"""
    drafted = {row.candidate_id for row in rows if row.status == "drafted" and row.candidate_id}
    states = _draft_states(conn, drafted)
    gone = [
        row.id
        for row in rows
        if row.status == "drafted" and (not row.candidate_id or row.candidate_id not in states)
    ]
    if gone:
        conn.execute(
            update(harvest_suggestions)
            .where(harvest_suggestions.c.id.in_(gone))
            .values(
                status="open",
                candidate_id="",
                decision_note="草稿已删除，回到待处理",
                updated_at=_now(),
            )
        )
        rows = conn.execute(
            select(harvest_suggestions)
            .where(harvest_suggestions.c.id.in_([row.id for row in rows]))
            .order_by(harvest_suggestions.c.created_at.desc())
        ).fetchall()
    return rows, states


def list_for_project(
    engine: Engine, principal: Principal, engagement_slug: str, status: str = ""
) -> list[dict[str, Any]]:
    """项目「资产 → 待沉淀」：本项目频道里的，加上我自己私聊里的。status 为空就全部。"""
    with engine.begin() as conn:
        rows = conn.execute(
            select(harvest_suggestions)
            .where(
                _visible_clause(principal, engagement_slug),
                *([harvest_suggestions.c.status == status] if status in STATUSES else []),
            )
            .order_by(harvest_suggestions.c.created_at.desc())
            .limit(500)
        ).fetchall()
        rows, states = _settle_drafts(conn, list(rows))
    return [
        public(row, states.get(row.candidate_id), principal)
        for row in rows
        if (not status or row.status == status) and may_see(principal, row)
    ]


def list_for_scope(
    engine: Engine, principal: Principal, scope: str, scope_id: str, status: str = "open"
) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(harvest_suggestions)
            .where(
                harvest_suggestions.c.scope == scope,
                harvest_suggestions.c.scope_id == scope_id,
                *([harvest_suggestions.c.status == status] if status in STATUSES else []),
            )
            .order_by(harvest_suggestions.c.created_at.desc())
            .limit(200)
        ).fetchall()
    return [public(row, None, principal) for row in rows if may_see(principal, row)]


def list_mine(engine: Engine, principal: Principal) -> list[dict[str, Any]]:
    """工作台用：我能看到的、还没处理的候选（跨项目）。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(harvest_suggestions)
            .where(harvest_suggestions.c.status == "open", _visible_clause(principal))
            .order_by(harvest_suggestions.c.created_at.desc())
            .limit(200)
        ).fetchall()
    return [public(row, None, principal) for row in rows]


def counts(engine: Engine, principal: Principal) -> dict[str, Any]:
    """红色数字：我能看到的「待处理」候选数，按项目分（待定不算）。处理了才减，看过不减。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(harvest_suggestions.c.engagement_slug, func.count())
            .where(harvest_suggestions.c.status == "open", _visible_clause(principal))
            .group_by(harvest_suggestions.c.engagement_slug)
        ).fetchall()
    by_project = {slug: int(count) for slug, count in rows}
    return {"total": sum(by_project.values()), "by_project": by_project}


def decide(
    engine: Engine,
    principal: Principal,
    suggestion_id: str,
    status: str,
    candidate_id: str = "",
    *,
    note: str = "",
    asset_id: str = "",
    merged_into: str = "",
) -> dict[str, Any]:
    """处理一条：
    drafted 起草了（带草稿编号）；linked 已有资产（带 asset_id）；duplicate 和另一条重复（带 merged_into，文件并过去）；
    ignored 不沉淀（可带原因）；hold 待定；open 恢复成待处理。
    """
    if status not in STATUSES:
        raise SuggestionError("处理方式只能是 open、hold、drafted、linked、duplicate、ignored")
    if status == "drafted" and not candidate_id:
        raise SuggestionError("起草要带上草稿编号")
    if status == "linked" and not asset_id:
        raise SuggestionError("要选一份覆盖它的已有资产")
    if status == "duplicate" and (not merged_into or merged_into == suggestion_id):
        raise SuggestionError("要选和它重复的另一条候选")
    now = _now()
    with engine.begin() as conn:
        row = conn.execute(
            select(harvest_suggestions).where(harvest_suggestions.c.id == suggestion_id)
        ).first()
        if row is None or not may_see(principal, row):
            raise SuggestionError("沉淀候选不存在或无权查看", 404)
        if not may_handle(principal, row):
            raise SuggestionError("只读成员只能查看，不能处理", 403)
        if (
            row.status == status
            and row.candidate_id == (candidate_id if status == "drafted" else "")
            and row.related_asset_id == (asset_id if status == "linked" else "")
            and row.merged_into == (merged_into if status == "duplicate" else "")
            and row.decision_note == (_text(note, 1000) if status != "open" else "")
        ):
            states = _draft_states(conn, {row.candidate_id} if row.candidate_id else set())
            return public(row, states.get(row.candidate_id), principal)
        if status == "duplicate":
            target = conn.execute(
                select(harvest_suggestions).where(harvest_suggestions.c.id == merged_into)
            ).first()
            if (
                target is None
                or not may_see(principal, target)
                or target.engagement_slug != row.engagement_slug
            ):
                raise SuggestionError("要合并到的候选不存在，或不在同一个项目里")
            if target.merged_into == suggestion_id:
                raise SuggestionError("这两条已经反过来合并过了")
            # 文件并过去；私聊的内容不并进频道候选（别人看得到），只记一次提到
            same_audience = (
                target.scope == row.scope
                and target.scope_id == row.scope_id
                and target.conversation_id == row.conversation_id
            )
            files = json.loads(target.files_json or "[]")
            if same_audience:
                files = list(dict.fromkeys([*files, *json.loads(row.files_json or "[]")]))
            mentions = json.loads(target.mentions_json or "[]")
            mentions.append(
                {
                    "source": row.source,
                    "by": row.created_by,
                    "scope": row.scope,
                    "at": now.isoformat(),
                    "message_id": row.message_id if same_audience else "",
                }
            )
            conn.execute(
                update(harvest_suggestions)
                .where(harvest_suggestions.c.id == target.id)
                .values(
                    files_json=json.dumps(files[:MAX_FILES], ensure_ascii=False),
                    mentions=(target.mentions or 0) + 1,
                    mentions_json=json.dumps(mentions[-MAX_MENTIONS:], ensure_ascii=False),
                    updated_at=now,
                )
            )
        conn.execute(
            update(harvest_suggestions)
            .where(harvest_suggestions.c.id == suggestion_id)
            .values(
                status=status,
                candidate_id=candidate_id if status == "drafted" else "",
                related_asset_id=asset_id if status == "linked" else "",
                merged_into=merged_into if status == "duplicate" else "",
                decision_note=_text(note, 1000) if status != "open" else "",
                decided_by=principal.user_id if status != "open" else "",
                decided_at=now if status != "open" else None,
                updated_at=now,
            )
        )
        record_event(
            conn,
            "harvest.suggestion_decided",
            {"id": suggestion_id, "status": status, "by": principal.user_id},
        )
        row = conn.execute(
            select(harvest_suggestions).where(harvest_suggestions.c.id == suggestion_id)
        ).first()
        states = _draft_states(conn, {row.candidate_id} if row.candidate_id else set())
    return public(row, states.get(row.candidate_id), principal)
