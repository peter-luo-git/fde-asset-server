"""订阅与变更通知。

两件事共用一套投递：
- **变更通知**：资产发了新版本，用过它的项目和 Agent 的负责人要知道改了什么
- **订阅**：我自己盯某个行业 / 类型 / 标签 / 某个人 / 某份资产，有新东西告诉我

资产服务只把通知落进站内收件箱，并发事件；要不要再推 IM 由 fde-server 决定——
谁拥有用户和会话，谁负责送达。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from fde_asset.core.db import (
    asset_notifications,
    asset_subscriptions,
    asset_versions,
    assets,
    record_event,
    target_assets,
)
from fde_asset.modules.asset.visibility import visibility_clause
from fde_asset.platform.identity import Principal

FILTER_TYPES = ("kind", "industry", "tag", "owner", "asset")
#: 一次变更最多通知多少人，防止一份公司规范改一次就炸出几百条
FANOUT_CAP = 200


class NotifyError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# —— 订阅 ——


def subscribe(engine: Engine, principal: Principal, filter_type: str, value: str) -> dict[str, Any]:
    if filter_type not in FILTER_TYPES:
        raise NotifyError(f"不支持的订阅类型：{filter_type}")
    if not value.strip():
        raise NotifyError("订阅内容不能为空")
    with engine.begin() as conn:
        exists = conn.execute(
            select(asset_subscriptions.c.subscription_id).where(
                and_(
                    asset_subscriptions.c.user_id == principal.user_id,
                    asset_subscriptions.c.filter_type == filter_type,
                    asset_subscriptions.c.filter_value == value,
                )
            )
        ).first()
        if exists is not None:
            return {"subscription_id": exists.subscription_id, "created": False}
        subscription_id = uuid.uuid4().hex[:16]
        conn.execute(
            asset_subscriptions.insert().values(
                subscription_id=subscription_id,
                user_id=principal.user_id,
                filter_type=filter_type,
                filter_value=value,
            )
        )
    return {"subscription_id": subscription_id, "created": True}


def unsubscribe(engine: Engine, principal: Principal, subscription_id: str) -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            select(asset_subscriptions).where(
                asset_subscriptions.c.subscription_id == subscription_id
            )
        ).first()
        if row is None or row.user_id != principal.user_id:
            return False
        conn.execute(
            asset_subscriptions.delete().where(
                asset_subscriptions.c.subscription_id == subscription_id
            )
        )
    return True


def list_subscriptions(engine: Engine, principal: Principal) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(asset_subscriptions).where(asset_subscriptions.c.user_id == principal.user_id)
        ).fetchall()
    return [
        {
            "subscription_id": row.subscription_id,
            "filter_type": row.filter_type,
            "filter_value": row.filter_value,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


# —— 版本快照与 diff ——


def _sections(text: str) -> dict[str, str]:
    """按 `## 标题` 切段，用来算改了哪几节。"""
    sections: dict[str, str] = {}
    current = ""
    buffer: list[str] = []
    for line in (text or "").splitlines():
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(buffer).strip()
            current = line[3:].strip()
            buffer = []
        elif current:
            buffer.append(line)
    if current:
        sections[current] = "\n".join(buffer).strip()
    return sections


def snapshot_version(engine: Engine, row: Any) -> bool:
    """给资产的当前内容留一份版本快照，返回这次有没有存。

    按**内容**判断要不要存，不按提交号：`commit_sha` 记的是整个仓库的最新提交，
    同仓库里别的资产一有提交它就变，按它存的话每份资产都会多出一份一模一样的版本。
    """
    sections_json = json.dumps(_sections(row.content_text or ""), ensure_ascii=False)
    try:
        return _snapshot_version(engine, row, sections_json)
    except IntegrityError:
        # 评审通过时会立刻建一次索引，后台的定时索引可能正好同时扫到同一次提交；
        # 两边都想存这一版，后到的那个撞上唯一约束——说明已经存好了，不算错。
        return False


def _snapshot_version(engine: Engine, row: Any, sections_json: str) -> bool:
    with engine.begin() as conn:
        latest = conn.execute(
            select(asset_versions)
            .where(asset_versions.c.asset_id == row.asset_id)
            .order_by(asset_versions.c.id.desc())
            .limit(1)
        ).first()
        if (
            latest is not None
            and latest.version == (row.version or "")
            and latest.summary == (row.summary or "")
            and latest.sections_json == sections_json
        ):
            return False
        same_commit = conn.execute(
            select(asset_versions.c.id).where(
                and_(
                    asset_versions.c.asset_id == row.asset_id,
                    asset_versions.c.commit_sha == (row.commit_sha or ""),
                )
            )
        ).first()
        if same_commit is not None:
            return False
        conn.execute(
            asset_versions.insert().values(
                asset_id=row.asset_id,
                version=row.version or "",
                commit_sha=row.commit_sha or "",
                summary=row.summary or "",
                sections_json=sections_json,
            )
        )
    return True


def version_count(engine: Engine, asset_id: str) -> int:
    with engine.connect() as conn:
        rows = conn.execute(
            select(asset_versions.c.id).where(asset_versions.c.asset_id == asset_id)
        ).fetchall()
    return len(rows)


def diff_summary(engine: Engine, asset_id: str) -> dict[str, Any]:
    """最近两版之间改了什么：章节级，够人判断要不要点开。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(asset_versions)
            .where(asset_versions.c.asset_id == asset_id)
            .order_by(asset_versions.c.id.desc())
            .limit(2)
        ).fetchall()
    if len(rows) < 2:
        return {"changed": [], "added": [], "removed": [], "summary_changed": False}
    new, old = rows[0], rows[1]
    new_sections = json.loads(new.sections_json or "{}")
    old_sections = json.loads(old.sections_json or "{}")
    return {
        "changed": sorted(
            name
            for name in new_sections.keys() & old_sections.keys()
            if new_sections[name] != old_sections[name]
        ),
        "added": sorted(new_sections.keys() - old_sections.keys()),
        "removed": sorted(old_sections.keys() - new_sections.keys()),
        "summary_changed": new.summary != old.summary,
        "from_version": old.version,
        "to_version": new.version,
    }


# —— 投递 ——


def _push(
    conn,
    *,
    user_id: str,
    kind: str,
    asset_id: str,
    title: str,
    body: str,
    reason: str,
) -> None:
    conn.execute(
        asset_notifications.insert().values(
            notification_id=uuid.uuid4().hex[:16],
            user_id=user_id,
            kind=kind,
            asset_id=asset_id,
            title=title,
            body=body,
            reason=reason,
        )
    )


def notify_review(
    engine: Engine,
    user_ids: list[str],
    *,
    candidate_id: str,
    title: str,
    body: str,
    reason: str,
) -> int:
    """评审相关的站内通知：有草稿等你评、你提交的草稿有结果了。

    这时资产还没入库（或刚入库），所以 `asset_id` 一栏放的是草稿编号，页面据 kind=review 跳到草稿页。
    """
    with engine.begin() as conn:
        for user_id in dict.fromkeys(user_ids):
            _push(
                conn,
                user_id=user_id,
                kind="review",
                asset_id=candidate_id,
                title=title,
                body=body,
                reason=reason,
            )
    return len(dict.fromkeys(user_ids))


def _subscribers(conn, row: Any) -> dict[str, str]:
    """哪些人订阅了这份资产会命中。返回 {用户: 命中的理由}。"""
    industries = set(json.loads(row.industry_json or "[]"))
    tags = set(json.loads(row.tags_json or "[]"))
    hit: dict[str, str] = {}
    for sub in conn.execute(select(asset_subscriptions)):
        value = sub.filter_value
        matched = (
            (sub.filter_type == "kind" and value == row.kind)
            or (sub.filter_type == "industry" and value in industries)
            or (sub.filter_type == "tag" and value in tags)
            or (sub.filter_type == "owner" and value == row.owner_ref)
            or (sub.filter_type == "asset" and value == row.asset_id)
        )
        if matched and sub.user_id not in hit:
            hit[sub.user_id] = f"你订阅了{sub.filter_type}={value}"
    return hit


def _users_using(conn, asset_id: str, owner_of: Any = None) -> dict[str, str]:
    """用过这份资产的项目与 Agent 的负责人。

    通知的是目标**现在的**负责人（`owner_of(类型, 标识)` 给出）；查不到时才退回当初做关联的人——
    项目会换负责人，关联也可能是部门主管推送后由别人接受的。
    """
    found: dict[str, str] = {}
    for row in conn.execute(select(target_assets).where(target_assets.c.asset_id == asset_id)):
        label = "项目" if row.target_type == "engagement" else "Agent"
        owner = ""
        if owner_of is not None:
            try:
                owner = owner_of(row.target_type, row.target_id) or ""
            except Exception:  # noqa: BLE001 - 名单查不到不该让通知整个失败
                owner = ""
        recipient = owner or row.created_by
        if recipient:
            found.setdefault(recipient, f"你的{label} {row.target_id} 关联了这份资产")
    return found


def announce_change(
    engine: Engine, asset_id: str, *, author: str = "", owner_of: Any = None
) -> dict[str, Any]:
    """资产更新后发通知：用过的人 + 订阅的人，各给各的理由。"""
    with engine.begin() as conn:
        row = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
        if row is None:
            raise NotifyError("资产不存在")
        diff = diff_summary(engine, asset_id)
        changed = diff["changed"] + diff["added"] + diff["removed"]
        body_parts = []
        if diff["summary_changed"]:
            body_parts.append("一句话结论改了")
        if diff["changed"]:
            body_parts.append("改动章节：" + "、".join(diff["changed"]))
        if diff["added"]:
            body_parts.append("新增章节：" + "、".join(diff["added"]))
        if diff["removed"]:
            body_parts.append("删掉章节：" + "、".join(diff["removed"]))
        body = "；".join(body_parts) or "内容有更新"

        audience: dict[str, str] = {}
        audience.update(_users_using(conn, asset_id, owner_of))
        for user_id, reason in _subscribers(conn, row).items():
            audience.setdefault(user_id, reason)
        audience.pop(author, None)  # 自己改的不用通知自己

        delivered = 0
        for user_id, reason in list(audience.items())[:FANOUT_CAP]:
            _push(
                conn,
                user_id=user_id,
                kind="asset_changed",
                asset_id=asset_id,
                title=f"{row.title} 更新了",
                body=body,
                reason=reason,
            )
            delivered += 1

        record_event(
            conn,
            "asset.changed",
            {
                "asset_id": asset_id,
                "delivered": delivered,
                "changed_sections": changed,
                "summary_changed": diff["summary_changed"],
            },
        )
    return {"delivered": delivered, "diff": diff, "body": body}


def announce_new(engine: Engine, asset_id: str, *, author: str = "") -> dict[str, Any]:
    """新资产入库：只通知订阅的人（用过的人还不存在）。"""
    with engine.begin() as conn:
        row = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
        if row is None:
            raise NotifyError("资产不存在")
        audience = _subscribers(conn, row)
        audience.pop(author, None)
        delivered = 0
        for user_id, reason in list(audience.items())[:FANOUT_CAP]:
            _push(
                conn,
                user_id=user_id,
                kind="asset_new",
                asset_id=asset_id,
                title=f"新资产：{row.title}",
                body=row.summary or "",
                reason=reason,
            )
            delivered += 1
    return {"delivered": delivered}


def inbox(
    engine: Engine, principal: Principal, *, unread_only: bool = True
) -> list[dict[str, Any]]:
    statement = select(asset_notifications).where(
        asset_notifications.c.user_id == principal.user_id
    )
    if unread_only:
        statement = statement.where(asset_notifications.c.read_at.is_(None))
    with engine.connect() as conn:
        rows = conn.execute(
            statement.order_by(asset_notifications.c.created_at.desc()).limit(100)
        ).fetchall()
        visible = {
            r.asset_id
            for r in conn.execute(select(assets.c.asset_id).where(visibility_clause(principal)))
        }
    return [
        {
            "notification_id": row.notification_id,
            "kind": row.kind,
            "asset_id": row.asset_id,
            "title": row.title,
            "body": row.body,
            "reason": row.reason,
            "created_at": row.created_at.isoformat(),
            "read": row.read_at is not None,
        }
        for row in rows
        # 通知发出之后资产可能被收紧了作用域，这里再挡一道；
        # 评审通知带的是草稿编号不是资产，草稿页自己会判权限
        if row.kind == "review" or not row.asset_id or row.asset_id in visible
    ]


def mark_read(engine: Engine, principal: Principal, notification_id: str) -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            select(asset_notifications).where(
                asset_notifications.c.notification_id == notification_id
            )
        ).first()
        if row is None or row.user_id != principal.user_id:
            return False
        conn.execute(
            asset_notifications.update()
            .where(asset_notifications.c.notification_id == notification_id)
            .values(read_at=_now())
        )
    return True


# —— 订阅时可以选什么 ——


def subscription_options(
    engine: Engine, principal: Principal, display_name_of: Any = None
) -> dict[str, list[dict[str, str]]]:
    """订阅表单的选项：这个人看得见的资产里实际出现过的行业、标签、负责人，以及资产本身。

    订阅是按值精确匹配的，让人手填等于让人猜系统里存的是什么（类型是 `Case` 不是「问题」，
    负责人是 `user:chen` 不是「小陈」），填错了不报错、只是永远不命中。所以给出现成的选项。
    """
    with engine.connect() as conn:
        rows = conn.execute(
            select(assets)
            .where(
                visibility_clause(principal),
                assets.c.valid.is_(True),
                assets.c.lifecycle.notin_(["deprecated", "archived", "draft"]),
            )
            .order_by(assets.c.title)
        ).fetchall()
    industries: set[str] = set()
    tags: set[str] = set()
    owners: dict[str, str] = {}
    kinds: set[str] = set()
    for row in rows:
        industries.update(json.loads(row.industry_json or "[]"))
        tags.update(json.loads(row.tags_json or "[]"))
        kinds.add(row.kind)
        if row.owner_ref and row.owner_ref not in owners:
            if row.owner_kind == "department":
                owners[row.owner_ref] = f"{row.owner_value} 部门"
            else:
                name = ""
                if display_name_of is not None:
                    try:
                        name = display_name_of(row.owner_value) or ""
                    except Exception:  # noqa: BLE001 - 名单里没有这个人就显示账号
                        name = ""
                owners[row.owner_ref] = name or row.owner_value
    return {
        "kind": [{"value": kind, "label": kind} for kind in sorted(kinds)],
        "industry": [{"value": item, "label": item} for item in sorted(industries) if item],
        "tag": [{"value": item, "label": item} for item in sorted(tags) if item],
        "owner": [
            {"value": ref, "label": label}
            for ref, label in sorted(owners.items(), key=lambda pair: pair[1])
        ],
        "asset": [
            {"value": row.asset_id, "label": row.title, "kind": row.kind} for row in rows[:500]
        ],
    }
