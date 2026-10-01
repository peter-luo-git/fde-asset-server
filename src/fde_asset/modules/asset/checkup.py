"""资产体检：资产会烂，烂资产比没有资产更伤信任。

四条规则，都只用库里已有的数据，不需要额外依赖：

| 规则 | 判据 | 为什么 |
|---|---|---|
| stale | 超过 N 天没更新 | 流程和事实都会变，长期不维护的内容会误导人 |
| unused | 入库超过 N 天但零复用 | 要么没人知道它，要么它没用，两种都该处理 |
| rejected | 推荐被拒 ≥2 次 | 摘要或适用边界写得不对，这是最精准的改进信号 |
| criticized | 收到过「没帮上」或「已过时」 | 用过的人直接说了不好用 |

体检只给出事实和建议，不自动改资产——判断还是人的事。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from fde_asset.core.db import asset_feedback, asset_recommendations, assets
from fde_asset.modules.asset.catalog import reuse_counts
from fde_asset.modules.asset.visibility import visibility_clause
from fde_asset.platform.identity import Principal

STALE_DAYS = 183
UNUSED_DAYS = 90
DECLINE_THRESHOLD = 2

SUGGESTION = {
    "stale": "半年没动了，确认一下内容还对不对；还有效就更新一次，过时了就标废弃",
    "unused": "入库三个月零复用：要么摘要没写清楚用处，要么它本来就不该单独成为资产",
    "rejected": "被多次拒绝，多半是摘要看不出用处或适用边界写得太宽",
    "criticized": "用过的人说不好用，看看他们的原话",
}


@dataclass
class Finding:
    asset_id: str
    title: str
    owner_ref: str
    rule: str
    detail: str
    suggestion: str


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def run(
    engine: Engine,
    principal: Principal,
    *,
    now: datetime | None = None,
    owner_only: bool = True,
) -> dict[str, Any]:
    """体检。owner_only=True 时只看我负责的（我负责的我才改得动）。"""
    moment = now or datetime.now(timezone.utc)
    owner_refs = {f"user:{principal.user_id}"}
    if principal.department_code:
        owner_refs.add(f"department:{principal.department_code}")

    with engine.connect() as conn:
        rows = conn.execute(
            select(assets).where(visibility_clause(principal), assets.c.valid.is_(True))
        ).fetchall()
        reuse = reuse_counts(conn)
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

    findings: list[Finding] = []
    for row in rows:
        if owner_only and row.owner_ref not in owner_refs:
            continue
        updated = _aware(row.updated_at)
        created = _aware(row.created_at)
        if updated and moment - updated > timedelta(days=STALE_DAYS):
            days = (moment - updated).days
            findings.append(
                Finding(
                    row.asset_id,
                    row.title,
                    row.owner_ref,
                    "stale",
                    f"{days} 天没更新",
                    SUGGESTION["stale"],
                )
            )
        if (
            created
            and moment - created > timedelta(days=UNUSED_DAYS)
            and reuse.get(row.asset_id, 0) == 0
        ):
            findings.append(
                Finding(
                    row.asset_id,
                    row.title,
                    row.owner_ref,
                    "unused",
                    "入库超过三个月，零复用",
                    SUGGESTION["unused"],
                )
            )
        times = declined.get(row.asset_id, 0)
        if times >= DECLINE_THRESHOLD:
            findings.append(
                Finding(
                    row.asset_id,
                    row.title,
                    row.owner_ref,
                    "rejected",
                    f"推荐被拒 {times} 次",
                    SUGGESTION["rejected"],
                )
            )
        complaints = criticized.get(row.asset_id, 0)
        if complaints:
            findings.append(
                Finding(
                    row.asset_id,
                    row.title,
                    row.owner_ref,
                    "criticized",
                    f"{complaints} 条负面反馈",
                    SUGGESTION["criticized"],
                )
            )

    tally: dict[str, int] = {}
    for item in findings:
        tally[item.rule] = tally.get(item.rule, 0) + 1
    return {
        "checked": len([r for r in rows if not owner_only or r.owner_ref in owner_refs]),
        "by_rule": tally,
        "items": [item.__dict__ for item in findings],
    }
