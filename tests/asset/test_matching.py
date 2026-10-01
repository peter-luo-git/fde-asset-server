"""资产匹配：给项目/Agent 上下文，推荐该带哪些资产，并说清为什么。"""

from __future__ import annotations

from fde_asset.modules.asset.matching import MatchContext, match, tokenize
from fde_asset.platform.identity import Membership, Principal

CHEN = Principal(
    "chen",
    department_code="data-intel",
    memberships=(Membership(engagement_slug="policy-import", department_code="finance"),),
)
ZHAO = Principal("zhao", department_code="market")


def test_tokenize_handles_chinese_and_english() -> None:
    tokens = tokenize("保单批量导入 Oracle migration")
    assert "导入" in tokens and "oracle" in tokens and "migration" in tokens
    # 单字不进切词，避免噪声
    assert "保" not in tokens


def test_rules_are_always_included_even_without_keyword_hit(indexed) -> None:
    """规范是红线：哪怕一个关键词都不沾，也必须带上。"""
    result = match(indexed.engine, CHEN, MatchContext(title="完全不相干的事情 xyzzy"))
    assert result["summary"]["rules"] > 0
    assert all("规范强制注入" in item["reasons"] for item in result["rules"])


def test_keyword_and_industry_raise_the_score(indexed) -> None:
    hit = match(
        indexed.engine,
        CHEN,
        MatchContext(title="保单批量导入超时", industry="insurance"),
    )
    titles = [item["title"] for item in hit["knowledge"]]
    assert titles, "应当推出知识类资产"
    top = hit["knowledge"][0]
    assert top["score"] > 0
    assert top["reasons"], "每条推荐都要有理由"
    assert any("关键词命中" in reason or "行业命中" in reason for reason in top["reasons"])


def test_unrelated_knowledge_is_not_recommended(indexed) -> None:
    result = match(indexed.engine, CHEN, MatchContext(title="xyzzy 完全不相干"))
    assert result["summary"]["knowledge"] == 0


def test_match_respects_visibility(indexed) -> None:
    """小赵看不到部门和项目资产，推荐里也不能出现。"""
    chen = match(indexed.engine, CHEN, MatchContext(title="保单导入", industry="insurance"))
    zhao = match(indexed.engine, ZHAO, MatchContext(title="保单导入", industry="insurance"))
    chen_ids = {
        item["asset_id"]
        for group in ("rules", "sops", "skills", "knowledge")
        for item in chen[group]
    }
    zhao_ids = {
        item["asset_id"]
        for group in ("rules", "sops", "skills", "knowledge")
        for item in zhao[group]
    }
    assert zhao_ids < chen_ids or len(zhao_ids) < len(chen_ids)


def test_group_limits_hold(indexed) -> None:
    result = match(indexed.engine, CHEN, MatchContext(title="导入 迁移 方案 问题 经验"))
    assert len(result["sops"]) <= 3
    assert len(result["skills"]) <= 8
    assert len(result["knowledge"]) <= 6


def test_refs_are_ready_to_paste(indexed) -> None:
    result = match(indexed.engine, CHEN, MatchContext(title="保单导入"))
    for item in result["rules"]:
        assert item["ref"].startswith("[[") and item["ref"].endswith("]]")


def test_department_alone_does_not_make_it_relevant(indexed) -> None:
    """只凭「本部门」不能把不相干的资产推出来，否则部门里所有资产都会被推。"""
    result = match(
        indexed.engine,
        CHEN,
        MatchContext(title="zzqqxx vvbbnn", department_code="data-intel"),
    )
    assert result["summary"]["knowledge"] == 0
    assert result["summary"]["skills"] == 0
    # 规范和流程仍然要有：红线必带，流程兜底
    assert result["summary"]["rules"] > 0
    assert result["summary"]["sops"] > 0
