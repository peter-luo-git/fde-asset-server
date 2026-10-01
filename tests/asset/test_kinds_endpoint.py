"""类型要求接口：前端标红必填项靠它，不能和服务端口径分叉。"""

from __future__ import annotations

from fde_asset.modules.asset.manifest import KIND_RULES


def test_lists_all_seven_kinds(client) -> None:
    items = client.get("/api/v1/kinds").json()["items"]
    assert {item["kind"] for item in items} == set(KIND_RULES)


def test_requirements_match_the_server_rules(client) -> None:
    items = {item["kind"]: item for item in client.get("/api/v1/kinds").json()["items"]}
    for kind, rule in KIND_RULES.items():
        assert items[kind]["requires_summary"] == rule.requires_summary
        assert items[kind]["requires_applicability"] == rule.requires_applicability
        assert items[kind]["required_sections"] == list(rule.required_sections)


def test_rule_kind_is_the_only_one_without_applicability(client) -> None:
    """规范是红线，没有「不适用」的说法；其余六种都必须写清适用边界。"""
    items = client.get("/api/v1/kinds").json()["items"]
    exempt = {item["kind"] for item in items if not item["requires_applicability"]}
    assert exempt == {"Rule"}
