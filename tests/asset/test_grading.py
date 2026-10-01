"""质量等级按复用自动评级（2026-10-01 确认的规则）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from fde_asset.core.db import assets
from fde_asset.modules.asset.grading import (
    GOLD,
    SILVER,
    grade_for,
    refresh_grades,
)
from fde_asset.modules.asset.usage import UsageInput, record_usages

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_grade_rule_boundaries() -> None:
    assert grade_for(0, NOW, NOW) == "bronze"
    assert grade_for(1, NOW, NOW) == "bronze"
    assert grade_for(2, NOW, NOW) == SILVER
    assert grade_for(3, NOW, NOW) == GOLD


def test_gold_falls_back_to_silver_when_stale() -> None:
    """半年不维护的资产自动退回银级，避免"一次评金、永远是金"。"""
    stale = NOW - timedelta(days=200)
    assert grade_for(5, stale, NOW) == SILVER
    assert grade_for(5, NOW - timedelta(days=100), NOW) == GOLD


def test_naive_updated_at_is_treated_as_utc() -> None:
    assert grade_for(3, datetime(2026, 9, 1), NOW) == GOLD


def test_refresh_grades_counts_every_asset(indexed) -> None:
    engine = indexed.engine
    tally = refresh_grades(engine, now=NOW)
    with engine.connect() as conn:
        total = conn.execute(
            select(assets.c.asset_id).where(assets.c.deleted_at.is_(None))
        ).fetchall()
    assert sum(tally.values()) == len(total)
    # 种子数据还没有跨项目复用，应该全是铜级
    assert tally[SILVER] == 0 and tally[GOLD] == 0


def test_grade_rises_after_two_other_engagements_use_it(indexed) -> None:
    engine = indexed.engine
    with engine.connect() as conn:
        row = conn.execute(
            select(assets.c.asset_id, assets.c.name).where(assets.c.kind == "Solution")
        ).first()
    assert row is not None
    asset_id, ref = row.asset_id, f"solution/{row.name}"

    def use(slug: str, session: str) -> None:
        result = record_usages(
            engine,
            [
                UsageInput(
                    ref=ref,
                    event="applied",
                    session_id=session,
                    engagement_slug=slug,
                    delivered_pr=True,
                )
            ],
        )
        assert result["accepted"] == 1, result

    def quality() -> str:
        with engine.connect() as conn:
            return conn.execute(
                select(assets.c.quality).where(assets.c.asset_id == asset_id)
            ).scalar_one()

    use("other-one", "s1")
    assert quality() == "bronze", "只有 1 个项目复用时仍是铜级"

    use("other-two", "s2")
    assert quality() == SILVER, "2 个项目复用升银"

    use("other-three", "s3")
    assert quality() == GOLD, "3 个项目复用且刚更新过升金"
