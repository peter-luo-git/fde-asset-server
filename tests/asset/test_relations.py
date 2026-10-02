"""资产关系：引用解析、自动建链、按可见性读图。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import asset_relations, assets
from fde_asset.modules.asset import relations
from fde_asset.platform.identity import Membership, Principal
from tests.conftest import as_user

CHEN = Principal(
    "chen",
    department_code="data-intel",
    memberships=(Membership(engagement_slug="policy-import", department_code="finance"),),
)
ZHAO = Principal("zhao", department_code="market")


def test_body_refs_get_resolved_to_real_ids(indexed) -> None:
    """正文里写 [[case/xxx]] 时对方可能还没入库，索引完要统一解析。"""
    with indexed.engine.connect() as conn:
        unresolved = conn.execute(
            select(asset_relations).where(
                asset_relations.c.source == "body", asset_relations.c.to_asset_id.is_(None)
            )
        ).fetchall()
        resolved = conn.execute(
            select(asset_relations).where(
                asset_relations.c.source == "body", asset_relations.c.to_asset_id.is_not(None)
            )
        ).fetchall()
    assert resolved, "正文引用应该有解析成功的"
    # 指向库里不存在的引用仍然留着，照实反映"引用了但没入库"
    assert all(row.to_ref for row in unresolved)


def test_same_engagement_assets_are_linked(indexed) -> None:
    result = relations.auto_link(indexed.engine)
    assert result["same_engagement"] > 0

    with indexed.engine.connect() as conn:
        row = conn.execute(
            select(assets).where(assets.c.engagement_slug == "policy-import")
        ).first()
    graph = relations.neighbours(indexed.engine, CHEN, row.asset_id)
    kinds = {item["type"] for item in graph["outgoing"]}
    assert "sameEngagement" in kinds
    assert all(item["label"] for item in graph["outgoing"]), "每条关系都要有中文说法"


def test_application_links_what_the_project_produced(indexed) -> None:
    """应用是项目的壳，它产出了什么要单独标出来。"""
    relations.auto_link(indexed.engine)
    with indexed.engine.connect() as conn:
        app = conn.execute(select(assets).where(assets.c.kind == "Application")).first()
    graph = relations.neighbours(indexed.engine, CHEN, app.asset_id)
    assert graph["outgoing"] or graph["incoming"], "应用该跟项目里的其它资产有关系"


def test_auto_link_is_idempotent(indexed) -> None:
    first = relations.auto_link(indexed.engine)
    second = relations.auto_link(indexed.engine)
    assert first["same_engagement"] == second["same_engagement"], "重复跑不该越连越多"


def test_graph_respects_visibility(indexed) -> None:
    """看不到的资产不能通过关系图漏出来。"""
    relations.auto_link(indexed.engine)
    with indexed.engine.connect() as conn:
        row = conn.execute(
            select(assets).where(assets.c.engagement_slug == "policy-import")
        ).first()
    chen = relations.neighbours(indexed.engine, CHEN, row.asset_id)
    zhao = relations.neighbours(indexed.engine, ZHAO, row.asset_id)
    assert len(zhao["outgoing"]) < len(chen["outgoing"])


def test_relations_endpoint(client) -> None:
    chen = as_user(client, "chen")
    asset_id = chen.get("/api/v1/assets", params={"limit": 1}).json()["items"][0]["asset_id"]
    payload = chen.get(f"/api/v1/assets/{asset_id}/relations").json()
    assert set(payload) == {"outgoing", "incoming", "unresolved"}


def test_relink_needs_permission(client) -> None:
    assert as_user(client, "chen").post("/api/v1/admin/assets/relink").status_code == 403
    assert as_user(client, "admin").post("/api/v1/admin/assets/relink").status_code == 200
