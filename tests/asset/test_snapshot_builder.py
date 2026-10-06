"""快照三层合并、知识索引与复用。"""

from __future__ import annotations

from fde_asset.modules.asset.snapshot import build_snapshot


def _build(context, **kwargs):
    return build_snapshot(
        context.engine, context.repo_port, snapshot_root=context.settings.snapshot_dir, **kwargs
    )


def test_snapshot_has_four_directories(indexed) -> None:
    result = _build(indexed, department_code="data-intel", engagement_slug="policy-import")
    for sub in ("rules", "skills", "sops", "knowledge"):
        assert (result.path / sub).exists()
    assert (result.path / "knowledge" / "INDEX.md").exists()


def test_three_layers_merged(indexed) -> None:
    result = _build(indexed, department_code="data-intel", engagement_slug="policy-import")
    skills = {p.name for p in (result.path / "skills").iterdir()}
    assert skills == {"insurance-policy-import", "pg-vacuum-tuning", "policy-date-parse"}


def test_company_only_snapshot_excludes_other_layers(indexed) -> None:
    result = _build(indexed)
    skills = {p.name for p in (result.path / "skills").iterdir()}
    assert skills == {"insurance-policy-import"}


def test_same_input_is_reused(indexed) -> None:
    first = _build(indexed, department_code="data-intel")
    second = _build(indexed, department_code="data-intel")
    assert first.sha == second.sha and second.reused


def test_invalid_assets_not_in_snapshot(indexed) -> None:
    result = _build(indexed)
    names = {p.name for p in (result.path / "knowledge" / "case").iterdir()}
    assert "no-summary-case" not in names


def test_index_is_truncated_when_over_limit(indexed) -> None:
    result = _build(indexed, index_limit=400)
    text = (result.path / "knowledge" / "INDEX.md").read_text(encoding="utf-8")
    assert result.index_truncated and "裁剪" in text


def test_attachment_text_written_for_agent(indexed) -> None:
    result = _build(indexed)
    extracted = result.path / "knowledge" / "impl" / "oracle-to-pg-cutover" / "attachments-text"
    assert (extracted / "baseline.xlsx.txt").exists()
    assert not list(result.path.rglob("*.xlsx"))  # 二进制不进快照


def test_customer_layer_sits_between_department_and_engagement() -> None:
    """合并顺序：公司 → 部门 → 客户 → 项目，越靠后越具体。"""
    from fde_asset.modules.asset.snapshot import _layer_order

    assert _layer_order("company") < _layer_order("department")
    assert _layer_order("department") < _layer_order("customer")
    assert _layer_order("customer") < _layer_order("engagement")


# ---------- 快照跟着关联走 ----------


def _link(context, target_id: str, *names: str, target_type: str = "engagement") -> None:
    from sqlalchemy import select

    from fde_asset.core.db import assets, target_assets

    with context.engine.begin() as conn:
        for name in names:
            asset_id = conn.execute(
                select(assets.c.asset_id).where(assets.c.name == name)
            ).scalar_one()
            conn.execute(
                target_assets.insert().values(
                    target_type=target_type,
                    target_id=target_id,
                    asset_id=asset_id,
                    source="direct",
                    created_by="wang",
                )
            )


def _names(result, sub: str) -> set[str]:
    folder = result.path / sub
    return {p.name for p in folder.iterdir()} if folder.exists() else set()


def _knowledge(result) -> set[str]:
    root = result.path / "knowledge"
    return {p.name for kind in root.iterdir() if kind.is_dir() for p in kind.iterdir()}


PROJECT = {"department_code": "finance", "engagement_slug": "policy-import"}
TARGET = {**PROJECT, "target_type": "engagement", "target_id": "policy-import"}


def test_without_links_everything_in_scope_is_carried(indexed) -> None:
    """还没关联过任何技能和知识的项目，不能因此什么都拿不到。"""
    plain = _build(indexed, **PROJECT)
    targeted = _build(indexed, **TARGET)
    assert targeted.selection == "scope" and targeted.linked == 0
    assert targeted.sha == plain.sha, "没有关联时和不给目标是同一份快照"


def test_linked_skills_and_knowledge_replace_the_whole_upper_layers(indexed) -> None:
    before = _build(indexed, **TARGET)
    assert "bank-core-migration-solution" in _knowledge(before)

    _link(indexed, "policy-import", "insurance-policy-import", "policy-import-poc-retro")
    after = _build(indexed, **TARGET)

    assert after.selection == "linked" and after.linked == 2
    assert after.sha != before.sha
    # 上层的技能和知识：只剩负责人挑过的
    assert "insurance-policy-import" in _names(after, "skills")
    knowledge = _knowledge(after)
    assert "policy-import-poc-retro" in knowledge
    assert "bank-core-migration-solution" not in knowledge, "没关联的公司级知识不再塞给 Agent"
    assert "oracle-to-pg-sequence-gap" not in knowledge
    index = (after.path / "knowledge" / "INDEX.md").read_text(encoding="utf-8")
    assert "保单导入 POC 复盘" in index and "银行核心迁移方案" not in index


def test_rules_sops_and_the_projects_own_assets_are_always_carried(indexed) -> None:
    baseline = _build(indexed, **TARGET)
    _link(indexed, "policy-import", "policy-import-poc-retro")
    linked = _build(indexed, **TARGET)

    assert _names(linked, "rules") == _names(baseline, "rules"), "规范是红线，不挑"
    assert _names(linked, "sops") == _names(baseline, "sops"), "流程分层继承要用到上层"
    # 项目自己沉淀的没关联也带着：那本来就是它的东西
    assert "policy-date-parse" in _names(linked, "skills")
    assert "import-over-10k-rows-timeout" in _knowledge(linked)
    # 公司级的技能没被关联，这次就不带了
    assert "insurance-policy-import" not in _names(linked, "skills")


def test_links_belong_to_their_target_only(indexed) -> None:
    _link(indexed, "policy-import", "policy-import-poc-retro")
    other = _build(
        indexed,
        department_code="finance",
        engagement_slug="core-migration",
        target_type="engagement",
        target_id="core-migration",
    )
    assert other.selection == "scope", "别的项目关联了什么，不影响这个项目"
    assert "bank-core-migration-solution" in _knowledge(other)


def test_linked_but_unavailable_assets_are_reported(indexed) -> None:
    """关联了却带不进来的要说出来：否则负责人以为 Agent 拿到了，其实没有。"""
    # 部门级技能属于 data-intel，而这个项目在 finance 部门下，不在它的作用域里
    _link(indexed, "policy-import", "policy-import-poc-retro", "pg-vacuum-tuning")
    result = _build(indexed, **TARGET)
    assert result.linked == 1
    assert "pg-vacuum-tuning" not in _names(result, "skills")
    reported = [item for item in result.skipped if item["reason"] == "linked_but_not_available"]
    assert [item["asset"] for item in reported] == ["PostgreSQL vacuum 调优"]

    again = _build(indexed, **TARGET)
    assert again.reused is True
    assert [item["asset"] for item in again.skipped if item["reason"].startswith("linked")] == [
        "PostgreSQL vacuum 调优"
    ], "复用已有快照时也要报"


def test_customer_layer_is_included_when_given(indexed) -> None:
    without = _build(indexed, **PROJECT)
    with_customer = _build(indexed, **PROJECT, customer_code="HUAAN")
    assert "huaan-gateway-throttling" not in _knowledge(without)
    assert "huaan-gateway-throttling" in _knowledge(with_customer)


def test_agent_target_uses_its_own_links(indexed) -> None:
    _link(indexed, "import-coder", "insurance-policy-import", target_type="agent")
    result = _build(
        indexed, department_code="data-intel", target_type="agent", target_id="import-coder"
    )
    assert result.selection == "linked"
    assert _names(result, "skills") == {"insurance-policy-import"}, "部门里没关联的技能不带"


def test_snapshot_endpoint_fills_scope_from_the_target(client) -> None:
    from tests.conftest import as_user

    wang = as_user(client, "wang")
    target = {"target_type": "engagement", "target_id": "policy-import"}
    first = wang.post("/api/v1/snapshots", json=target).json()
    assert first["selection"] == "scope" and first["linked"] == 0
    # 没传部门、客户：从名单里补出来，所以项目级和客户级的资产都在
    assert first["counts"].get("Case", 0) >= 3

    item = next(
        i
        for i in wang.post("/api/v1/recommend/compute", json=target).json()["items"]
        if i["group"] == "knowledge" and i["scope"] == "company"
    )
    assert (
        wang.post(
            "/api/v1/recommend/link", json={**target, "asset_id": item["asset_id"]}
        ).status_code
        == 200
    )
    second = wang.post("/api/v1/snapshots", json=target).json()
    assert second["selection"] == "linked" and second["linked"] == 1
    assert second["sha"] != first["sha"]

    assert (
        wang.post("/api/v1/snapshots", json={"target_type": "team", "target_id": "x"}).status_code
        == 422
    )
