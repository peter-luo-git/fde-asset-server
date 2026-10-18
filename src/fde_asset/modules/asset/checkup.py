"""资产体检：资产会烂，烂资产比没有资产更伤信任。

| 规则 | 判据 | 为什么 |
|---|---|---|
| stale | 超过半年没更新 | 流程和事实都会变，长期不维护的内容会误导人 |
| unused | 三个月零复用 | 要么没人知道它，要么它没用，两种都该处理 |
| rejected | 推荐被拒 ≥2 次 | 摘要或适用边界写得不对，这是最精准的改进信号 |
| criticized | 收到过「没帮上」或「已过时」 | 用过的人直接说了不好用 |
| broken_ref | 正文或元数据里的 `[[引用]]` 在库里找不到 | 读的人和 Agent 顺着引用走会走到死胡同 |
| owner_gone | 负责人已不在平台（离职或停用） | 没人维护，也没人能处理别的问题 |
| named_gone | 可见范围里点名的人已不在平台 | 名单该收回了（B5） |
| named_moved | 点名的人原来在这份资产相关的项目或部门里，现在不在了 | 人员变动，看要不要收回（B5） |

体检只给出事实和建议，不自动改资产——判断还是人的事。

两种用法：
- 页面打开时现算（`run`），只看我负责的；
- 定时任务每天一次（`sweep`），全量算、结果落库，**新出现**的问题给负责人发一条站内通知（B10，
  2026-10-18）。人员相关的三条要平台的人员名单：名单读不到时这三条不算，也不会因此被当成"已解决"。

**零复用从哪天算**：复用数来自平台上报的使用记录。上报开始之前入库的资产，不能因为那段时间
没有记录就算零复用，所以从"入库时间"和"第一条使用记录的时间"里取较晚的那个算三个月；一条使用
记录都没有时，这条规则不提示。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Engine

from fde_asset.core.db import (
    asset_audiences,
    asset_checkup_findings,
    asset_feedback,
    asset_named_audience,
    asset_recommendations,
    asset_relations,
    asset_usages,
    assets,
)
from fde_asset.modules.asset.catalog import reuse_counts
from fde_asset.modules.asset.visibility import visibility_clause
from fde_asset.platform.identity import Principal

logger = logging.getLogger(__name__)

STALE_DAYS = 183
UNUSED_DAYS = 90
DECLINE_THRESHOLD = 2

LABEL = {
    "stale": "太久没更新",
    "unused": "零复用",
    "rejected": "反复被拒",
    "criticized": "收到差评",
    "broken_ref": "引用失效",
    "owner_gone": "负责人已不在",
    "named_gone": "点名的人已不在",
    "named_moved": "点名的人已调离",
}

SUGGESTION = {
    "stale": "半年没动了，确认一下内容还对不对；还有效就更新一次，过时了就标废弃",
    "unused": "三个月零复用：要么摘要没写清楚用处，要么它本来就不该单独成为资产",
    "rejected": "被多次拒绝，多半是摘要看不出用处或适用边界写得太宽",
    "criticized": "用过的人说不好用，看看他们的原话",
    "broken_ref": "引用的资产还没入库、改了标识或被删了；改成现在的标识，或者去掉这条引用",
    "owner_gone": "负责人已经不在平台上，请把负责人改成现在维护它的人或部门",
    "named_gone": "这个人已经不在平台上，从可见范围里去掉",
    "named_moved": "这个人已经不在相关的项目或部门里，确认还要不要给他看",
}

#: 要平台人员名单才算得出来的规则；名单读不到时不算，也不当作已解决
PEOPLE_RULES = {"owner_gone", "named_gone", "named_moved"}


@dataclass
class Finding:
    asset_id: str
    title: str
    owner_ref: str
    rule: str
    detail: str
    suggestion: str
    subject: str = ""


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _people(directory: Any) -> dict[str, Principal] | None:
    """平台上现在在的人（名单只给在职、启用的）；读不到返回 None。"""
    if directory is None:
        return None
    try:
        return {user_id: directory.resolve(user_id) for user_id in directory.users()}
    except Exception as error:  # noqa: BLE001 - 名单读不到只是少算三条规则
        logger.warning("体检读不到人员名单，人员相关的规则这次不算：%s", error)
        return None


def related(person: Principal, row: Any, audiences: list[tuple[str, str]]) -> bool:
    """这个人和这份资产还有没有关系：在它的来源项目、负责部门，或可见范围里的部门、项目、客户里。"""
    if row.engagement_slug and row.engagement_slug in person.engagement_slugs:
        return True
    if row.owner_kind == "department" and row.owner_value in person.department_codes:
        return True
    for audience, code in audiences:
        if audience == "department" and code in person.department_codes:
            return True
        if audience == "engagement" and code in person.engagement_slugs:
            return True
        if audience == "customer" and code in person.customer_codes:
            return True
    return False


def _facts(conn) -> dict[str, Any]:
    declined = {
        row.asset_id: row.times
        for row in conn.execute(
            select(asset_recommendations.c.asset_id, func.count().label("times"))
            .where(asset_recommendations.c.status == "declined")
            .group_by(asset_recommendations.c.asset_id)
        )
    }
    criticized = {
        row.asset_id: row.times
        for row in conn.execute(
            select(asset_feedback.c.asset_id, func.count().label("times"))
            .where(asset_feedback.c.verdict.in_(["not_helpful", "outdated"]))
            .group_by(asset_feedback.c.asset_id)
        )
    }
    broken: dict[str, list[str]] = {}
    for row in conn.execute(
        select(asset_relations.c.from_asset_id, asset_relations.c.to_ref).where(
            asset_relations.c.to_asset_id.is_(None)
        )
    ):
        if row.to_ref and row.to_ref not in broken.setdefault(row.from_asset_id, []):
            broken[row.from_asset_id].append(row.to_ref)
    audiences: dict[str, list[tuple[str, str]]] = {}
    for row in conn.execute(select(asset_audiences)):
        audiences.setdefault(row.asset_id, []).append((row.audience, row.code))
    usage_started = conn.execute(
        select(func.min(asset_usages.c.created_at)).where(asset_usages.c.event != "cited")
    ).scalar()
    baseline = {
        (row.asset_id, row.user_id): row.related
        for row in conn.execute(select(asset_named_audience))
    }
    return {
        "reuse": reuse_counts(conn),
        "declined": declined,
        "criticized": criticized,
        "broken": broken,
        "audiences": audiences,
        "usage_started": _aware(usage_started),
        "baseline": baseline,
    }


def _evaluate(
    rows: list[Any], facts: dict[str, Any], moment: datetime, people: dict[str, Principal] | None
) -> list[Finding]:
    findings: list[Finding] = []

    def add(row: Any, rule: str, detail: str, subject: str = "") -> None:
        findings.append(
            Finding(row.asset_id, row.title, row.owner_ref, rule, detail, SUGGESTION[rule], subject)
        )

    usage_started = facts["usage_started"]
    for row in rows:
        updated = _aware(row.updated_at)
        created = _aware(row.created_at)
        if updated and moment - updated > timedelta(days=STALE_DAYS):
            add(row, "stale", f"{(moment - updated).days} 天没更新")
        if created and usage_started and facts["reuse"].get(row.asset_id, 0) == 0:
            since = max(created, usage_started)
            if moment - since > timedelta(days=UNUSED_DAYS):
                add(
                    row,
                    "unused",
                    "入库超过三个月，零复用"
                    if since == created
                    else "平台开始记录使用情况已超过三个月，这期间零复用",
                )
        times = facts["declined"].get(row.asset_id, 0)
        if times >= DECLINE_THRESHOLD:
            add(row, "rejected", f"推荐被拒 {times} 次")
        complaints = facts["criticized"].get(row.asset_id, 0)
        if complaints:
            add(row, "criticized", f"{complaints} 条负面反馈")
        for ref in facts["broken"].get(row.asset_id, []):
            add(row, "broken_ref", f"引用了 [[{ref}]]，库里找不到", ref)
        if people is None:
            continue
        if row.owner_kind == "user" and row.owner_value and row.owner_value not in people:
            add(row, "owner_gone", f"负责人 {row.owner_value} 已不在平台上", row.owner_value)
        audiences = facts["audiences"].get(row.asset_id, [])
        for audience, user_id in audiences:
            if audience != "user" or not user_id:
                continue
            person = people.get(user_id)
            if person is None:
                add(row, "named_gone", f"可见范围里点名的 {user_id} 已不在平台上", user_id)
            elif facts["baseline"].get((row.asset_id, user_id)) and not related(
                person, row, audiences
            ):
                add(
                    row,
                    "named_moved",
                    f"{person.display_name or user_id} 已不在这份资产相关的项目或部门里",
                    user_id,
                )
    return findings


def _tally(findings: list[Finding]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for item in findings:
        tally[item.rule] = tally.get(item.rule, 0) + 1
    return tally


def run(
    engine: Engine,
    principal: Principal,
    *,
    now: datetime | None = None,
    owner_only: bool = True,
    directory: Any = None,
) -> dict[str, Any]:
    """页面上的体检，现算。owner_only=True 时只看我负责的（我负责的我才改得动）。"""
    moment = now or datetime.now(timezone.utc)
    owner_refs = {f"user:{principal.user_id}"}
    if principal.department_code:
        owner_refs.add(f"department:{principal.department_code}")
    with engine.connect() as conn:
        rows = [
            row
            for row in conn.execute(
                select(assets).where(visibility_clause(principal), assets.c.valid.is_(True))
            ).fetchall()
            if not owner_only or row.owner_ref in owner_refs
        ]
        facts = _facts(conn)
        first_seen = {
            (row.asset_id, row.rule, row.subject): row.first_seen_at
            for row in conn.execute(
                select(asset_checkup_findings).where(asset_checkup_findings.c.resolved_at.is_(None))
            )
        }
    findings = _evaluate(rows, facts, moment, _people(directory))
    items = []
    for item in findings:
        data = dict(item.__dict__)
        seen = first_seen.get((item.asset_id, item.rule, item.subject))
        data["first_seen_at"] = seen.isoformat() if seen else None
        items.append(data)
    return {"checked": len(rows), "by_rule": _tally(findings), "items": items}


def _recipients(owner_ref: str, rule: str, people: dict[str, Principal] | None) -> list[str]:
    """谁该收到这条体检通知：个人负责的是本人；部门负责的是该部门的主管和资产评审员；
    负责人已不在、或部门里找不到人时，交给资产评审员。"""
    kind, _, value = owner_ref.partition(":")
    if people is None:
        return [value] if kind == "user" and value else []
    if rule != "owner_gone" and kind == "user" and value in people:
        return [value]
    if rule != "owner_gone" and kind == "department":
        heads = [
            user_id
            for user_id, person in people.items()
            if person.department_code == value
            and (person.is_department_head or person.is_asset_reviewer)
        ]
        if heads:
            return sorted(heads)
    return sorted(user_id for user_id, person in people.items() if person.is_asset_reviewer)


def sweep(
    engine: Engine,
    directory: Any,
    *,
    now: datetime | None = None,
    notify: bool = True,
) -> dict[str, int]:
    """定时体检：全量算一遍，结果落库；新出现的问题按负责人汇总成一条站内通知。"""
    from fde_asset.modules.notify import service as notify_service

    moment = now or datetime.now(timezone.utc)
    people = _people(directory)
    with engine.connect() as conn:
        rows = conn.execute(
            select(assets).where(assets.c.valid.is_(True), assets.c.deleted_at.is_(None))
        ).fetchall()
        facts = _facts(conn)
    findings = _evaluate(rows, facts, moment, people)
    by_key = {(item.asset_id, item.rule, item.subject): item for item in findings}
    by_asset = {row.asset_id: row for row in rows}
    counts = {"checked": len(rows), "open": len(findings), "new": 0, "resolved": 0, "notified": 0}
    fresh: list[Finding] = []

    with engine.begin() as conn:
        # 点名的人这次和资产还有没有关系，记下来，下次比较（名单读不到时不动）
        if people is not None:
            named = {
                (asset_id, code)
                for asset_id, entries in facts["audiences"].items()
                for audience, code in entries
                if audience == "user" and code and asset_id in by_asset
            }
            for key in set(facts["baseline"]) - named:
                conn.execute(
                    asset_named_audience.delete().where(
                        and_(
                            asset_named_audience.c.asset_id == key[0],
                            asset_named_audience.c.user_id == key[1],
                        )
                    )
                )
            for asset_id, user_id in named:
                person = people.get(user_id)
                if person is None:
                    continue
                now_related = related(person, by_asset[asset_id], facts["audiences"][asset_id])
                if (asset_id, user_id) in facts["baseline"]:
                    # 只会从"没关系"升成"有关系"（后来加入了相关项目），不会降回去：
                    # 调离之后要一直提醒，直到负责人把人从可见范围里去掉，或者他又回来了
                    values: dict[str, Any] = {"checked_at": moment}
                    if now_related:
                        values["related"] = True
                    conn.execute(
                        asset_named_audience.update()
                        .where(
                            and_(
                                asset_named_audience.c.asset_id == asset_id,
                                asset_named_audience.c.user_id == user_id,
                            )
                        )
                        .values(**values)
                    )
                else:
                    conn.execute(
                        asset_named_audience.insert().values(
                            asset_id=asset_id,
                            user_id=user_id,
                            related=now_related,
                            checked_at=moment,
                        )
                    )

        stored = {
            (row.asset_id, row.rule, row.subject): row
            for row in conn.execute(select(asset_checkup_findings))
        }
        for key, item in by_key.items():
            row = stored.get(key)
            if row is None:
                conn.execute(
                    asset_checkup_findings.insert().values(
                        asset_id=item.asset_id,
                        rule=item.rule,
                        subject=item.subject,
                        owner_ref=item.owner_ref,
                        detail=item.detail,
                        first_seen_at=moment,
                        last_seen_at=moment,
                    )
                )
                fresh.append(item)
            elif row.resolved_at is not None:
                conn.execute(
                    asset_checkup_findings.update()
                    .where(asset_checkup_findings.c.id == row.id)
                    .values(
                        owner_ref=item.owner_ref,
                        detail=item.detail,
                        first_seen_at=moment,
                        last_seen_at=moment,
                        resolved_at=None,
                        notified_at=None,
                    )
                )
                fresh.append(item)
            else:
                conn.execute(
                    asset_checkup_findings.update()
                    .where(asset_checkup_findings.c.id == row.id)
                    .values(owner_ref=item.owner_ref, detail=item.detail, last_seen_at=moment)
                )
        for key, row in stored.items():
            if key in by_key or row.resolved_at is not None:
                continue
            if people is None and row.rule in PEOPLE_RULES:
                continue  # 这次没法判断，不算解决
            conn.execute(
                asset_checkup_findings.update()
                .where(asset_checkup_findings.c.id == row.id)
                .values(resolved_at=moment)
            )
            counts["resolved"] += 1
        if fresh:
            for item in fresh:
                conn.execute(
                    asset_checkup_findings.update()
                    .where(
                        and_(
                            asset_checkup_findings.c.asset_id == item.asset_id,
                            asset_checkup_findings.c.rule == item.rule,
                            asset_checkup_findings.c.subject == item.subject,
                        )
                    )
                    .values(notified_at=moment)
                )
    counts["new"] = len(fresh)
    if not notify or not fresh:
        return counts

    # 一个人一条通知：第一次跑会把存量问题都当成新的，逐条发会刷屏
    per_user: dict[str, list[Finding]] = {}
    for item in fresh:
        for user_id in _recipients(item.owner_ref, item.rule, people):
            per_user.setdefault(user_id, []).append(item)
    for user_id, items in per_user.items():
        assets_hit = list(dict.fromkeys(item.asset_id for item in items))
        lines = [f"{item.title}：{LABEL[item.rule]}（{item.detail}）" for item in items[:5]]
        if len(items) > 5:
            lines.append(f"……还有 {len(items) - 5} 条")
        notify_service.notify_users(
            engine,
            [user_id],
            kind="checkup",
            asset_id=assets_hit[0] if len(assets_hit) == 1 else "",
            title=(
                f"资产体检：{items[0].title}"
                if len(assets_hit) == 1
                else f"资产体检发现 {len(items)} 个新问题"
            ),
            body="\n".join(lines),
            reason="你负责的资产"
            if not any(i.rule == "owner_gone" for i in items)
            else "资产评审员",
        )
        counts["notified"] += 1
    return counts
