"""质量等级：按复用情况自动评级，不靠人工打分。

规则（2026-10-01 确认）：

| 等级 | 条件 |
|---|---|
| 铜 bronze | 已入库（通过评审即入库），默认等级 |
| 银 silver | 被来源项目之外的 2 个以上项目复用 |
| 金 gold | 被 3 个以上项目复用，**且**近半年有更新 |

为什么不人工定级：人工标准难统一、要持续维护，而复用次数是现成的客观数据。
"金"额外要求"近半年有更新"，是为了让长期不维护的资产自动退回银级。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets

BRONZE = "bronze"
SILVER = "silver"
GOLD = "gold"

SILVER_REUSE = 2
GOLD_REUSE = 3
GOLD_FRESH_DAYS = 183

#: 给前端展示用的规则说明，避免两边各写一份口径
RULE_TEXT = "铜=已入库；银=被 2 个以上项目复用；金=被 3 个以上项目复用且近半年有更新"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def grade_for(reuse_engagement_count: int, updated_at: datetime, now: datetime) -> str:
    """单个资产的等级。updated_at 可以是裸时间，按 UTC 处理。"""
    if reuse_engagement_count >= GOLD_REUSE:
        fresh = _aware(now) - _aware(updated_at) <= timedelta(days=GOLD_FRESH_DAYS)
        return GOLD if fresh else SILVER
    if reuse_engagement_count >= SILVER_REUSE:
        return SILVER
    return BRONZE


def refresh_grades(engine: Engine, *, now: datetime | None = None) -> dict[str, int]:
    """重算所有资产的等级并落库，返回各等级的数量。

    复用数变化（新的使用事件或引用）、重建索引之后调用。等级落库而不是读时计算，
    是为了让"按等级筛选"仍然能下推到 SQL。
    """
    from fde_asset.modules.asset.catalog import reuse_counts

    moment = now or datetime.now(timezone.utc)
    tally = {BRONZE: 0, SILVER: 0, GOLD: 0}
    with engine.begin() as conn:
        counts = reuse_counts(conn)
        rows = conn.execute(
            select(assets.c.asset_id, assets.c.quality, assets.c.updated_at).where(
                assets.c.deleted_at.is_(None)
            )
        ).fetchall()
        for row in rows:
            wanted = grade_for(counts.get(row.asset_id, 0), row.updated_at, moment)
            tally[wanted] += 1
            if wanted != row.quality:
                conn.execute(
                    assets.update().where(assets.c.asset_id == row.asset_id).values(quality=wanted)
                )
    return tally
