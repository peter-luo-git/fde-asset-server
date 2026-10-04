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


def _id(engine, name: str) -> str:
    with engine.connect() as conn:
        return conn.execute(select(assets.c.asset_id).where(assets.c.name == name)).scalar_one()


def _link(engine, source: str, target: str, relation_type: str = "implements") -> None:
    with engine.begin() as conn:
        conn.execute(
            asset_relations.insert().values(
                from_asset_id=source,
                to_ref="manual",
                to_asset_id=target,
                type=relation_type,
                source="metadata",
            )
        )


def test_subgraph_grows_with_depth(indexed) -> None:
    """图谱比列表多出来的就是「再往外一层」。"""
    engine = indexed.engine
    solution = _id(engine, "bank-core-migration-solution")
    cutover = _id(engine, "oracle-to-pg-cutover")
    gap = _id(engine, "oracle-to-pg-sequence-gap")
    with engine.begin() as conn:
        conn.execute(asset_relations.delete())
    _link(engine, cutover, solution, "implements")
    _link(engine, cutover, gap, "produced")

    one = relations.graph(engine, CHEN, solution, depth=1)
    two = relations.graph(engine, CHEN, solution, depth=2)

    assert one["root"] == solution
    assert {node["asset_id"] for node in one["nodes"]} == {solution, cutover}
    assert {node["asset_id"] for node in two["nodes"]} == {solution, cutover, gap}
    depth_of = {node["asset_id"]: node["depth"] for node in two["nodes"]}
    assert depth_of == {solution: 0, cutover: 1, gap: 2}
    assert {(edge["from"], edge["to"], edge["label"]) for edge in two["edges"]} == {
        (cutover, solution, "实施了"),
        (cutover, gap, "项目产出"),
    }
    assert all(edge["directed"] for edge in two["edges"])


def test_subgraph_draws_undirected_relations_once(indexed) -> None:
    relations.auto_link(indexed.engine)
    root = _id(indexed.engine, "import-over-10k-rows-timeout")
    result = relations.graph(indexed.engine, CHEN, root, depth=1)
    same = [edge for edge in result["edges"] if edge["type"] == "sameEngagement"]
    assert same, "同项目沉淀的资产应该连上"
    pairs = [frozenset((edge["from"], edge["to"])) for edge in same]
    assert len(pairs) == len(set(pairs)), "A→B 和 B→A 只画一条线"
    assert not any(edge["directed"] for edge in same)


def test_hidden_asset_is_neither_a_node_nor_a_stepping_stone(indexed) -> None:
    """公司资产 → 项目资产 → 公司资产：看不到中间那份的人，不能顺着它摸到后面。"""
    engine = indexed.engine
    solution = _id(engine, "bank-core-migration-solution")
    hidden = _id(engine, "import-over-10k-rows-timeout")
    beyond = _id(engine, "oracle-to-pg-sequence-gap")
    with engine.begin() as conn:
        conn.execute(asset_relations.delete())
    _link(engine, solution, hidden)
    _link(engine, hidden, beyond)

    chen = {node["asset_id"] for node in relations.graph(engine, CHEN, solution, depth=3)["nodes"]}
    zhao = relations.graph(engine, ZHAO, solution, depth=3)

    assert chen == {solution, hidden, beyond}
    assert {node["asset_id"] for node in zhao["nodes"]} == {solution}
    assert zhao["edges"] == []
    assert relations.graph(engine, ZHAO, hidden, depth=1) is None, "中心资产看不到就整张图都不给"


def test_subgraph_is_capped(indexed, monkeypatch) -> None:
    relations.auto_link(indexed.engine)
    monkeypatch.setattr(relations, "GRAPH_MAX_NODES", 2)
    root = _id(indexed.engine, "import-over-10k-rows-timeout")
    result = relations.graph(indexed.engine, CHEN, root, depth=2)
    assert len(result["nodes"]) == 2
    assert result["truncated"] is True
    ids = {node["asset_id"] for node in result["nodes"]}
    assert all(edge["from"] in ids and edge["to"] in ids for edge in result["edges"])


def test_graph_endpoint(client) -> None:
    chen = as_user(client, "chen")
    items = chen.get("/api/v1/assets", params={"scope": "engagement"}).json()["items"]
    asset_id = items[0]["asset_id"]
    payload = chen.get(f"/api/v1/assets/{asset_id}/graph", params={"depth": 2}).json()
    assert set(payload) == {"root", "depth", "truncated", "nodes", "edges"}
    assert payload["nodes"][0]["asset_id"] == asset_id and payload["nodes"][0]["depth"] == 0

    assert chen.get(f"/api/v1/assets/{asset_id}/graph", params={"depth": 9}).status_code == 422
    zhao = as_user(client, "zhao")
    assert zhao.get(f"/api/v1/assets/{asset_id}/graph").status_code == 404
