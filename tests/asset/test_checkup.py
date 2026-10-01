"""资产体检：半年没更新、零复用、被反复拒绝、收到差评。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from fde_asset.core.db import assets
from fde_asset.modules.asset import checkup
from fde_asset.platform.identity import Principal
from tests.conftest import as_user

CHEN = Principal("chen", department_code="data-intel")
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _my_asset(engine):
    with engine.connect() as conn:
        return conn.execute(select(assets).where(assets.c.owner_ref == "user:chen")).first()


def test_fresh_assets_are_not_flagged_as_stale(indexed) -> None:
    result = checkup.run(indexed.engine, CHEN, now=NOW)
    assert "stale" not in result["by_rule"], "刚建好的资产不该被判定为过期"


def test_half_year_without_update_is_flagged(indexed) -> None:
    row = _my_asset(indexed.engine)
    with indexed.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(updated_at=NOW - timedelta(days=200))
        )
    result = checkup.run(indexed.engine, CHEN, now=NOW)
    stale = [item for item in result["items"] if item["rule"] == "stale"]
    assert any(item["asset_id"] == row.asset_id for item in stale)
    assert "半年没动" in stale[0]["suggestion"]


def test_three_months_without_reuse_is_flagged(indexed) -> None:
    row = _my_asset(indexed.engine)
    with indexed.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(created_at=NOW - timedelta(days=120))
        )
    result = checkup.run(indexed.engine, CHEN, now=NOW)
    assert any(
        item["rule"] == "unused" and item["asset_id"] == row.asset_id for item in result["items"]
    )


def test_repeated_rejections_and_complaints_are_flagged(client) -> None:
    """被拒和差评都要进体检——它们是最直接的改进信号。"""
    chen = as_user(client, "chen")
    mine = chen.get("/api/v1/assets", params={"mine": "personal", "limit": 1}).json()["items"][0]
    chen.post(
        f"/api/v1/assets/{mine['asset_id']}/feedback",
        json={"verdict": "not_helpful", "note": "对不上"},
    )

    result = as_user(client, "chen").get("/api/v1/governance/checkup").json()
    assert any(
        item["rule"] == "criticized" and item["asset_id"] == mine["asset_id"]
        for item in result["items"]
    )


def test_checkup_only_covers_what_i_can_fix(indexed) -> None:
    """默认只看我负责的——别人的资产我改不动，列出来只是噪声。"""
    mine = checkup.run(indexed.engine, CHEN, now=NOW, owner_only=True)
    everything = checkup.run(indexed.engine, CHEN, now=NOW, owner_only=False)
    assert mine["checked"] < everything["checked"]


def test_each_finding_carries_a_suggestion(indexed) -> None:
    row = _my_asset(indexed.engine)
    with indexed.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.asset_id == row.asset_id)
            .values(updated_at=NOW - timedelta(days=400), created_at=NOW - timedelta(days=400))
        )
    result = checkup.run(indexed.engine, CHEN, now=NOW)
    assert result["items"], "应当有发现"
    assert all(item["suggestion"] for item in result["items"]), "每条发现都要给出该怎么办"
