"""内容理解重排：模型挑，模型写理由；模型不在或抽风就退回关键词排序。"""

from __future__ import annotations

import json
import os

import pytest

from fde_asset.modules.asset import rerank
from fde_asset.platform.llm.client import (
    NullLlmClient,
    build_client,
    config_from_env,
    load_env_file,
    _loads,
)
from pathlib import Path

CANDIDATES = [
    {
        "asset_id": "a1",
        "kind": "Case",
        "title": "导入超过 1 万行报错",
        "summary": "单事务过大导致超时，按 2000 行分批提交",
        "score": 3.0,
        "reasons": ["关键词命中 导入"],
    },
    {
        "asset_id": "a2",
        "kind": "Skill",
        "title": "PostgreSQL vacuum 调优",
        "summary": "大表批量写入后的膨胀治理",
        "score": 2.0,
        "reasons": ["本部门资产"],
    },
    {
        "asset_id": "a3",
        "kind": "Experience",
        "title": "切换窗口谈判",
        "summary": "怎么把割接窗口谈到周末凌晨",
        "score": 1.0,
        "reasons": [],
    },
]

CONTEXT = {"标题": "保单导入超时", "描述": "导入 12000 行报 statement timeout", "行业": "insurance"}


class FakeLlm:
    usable = True

    def __init__(self, payload: dict | Exception) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.prompts.append(user)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_without_model_falls_back_to_keyword_order() -> None:
    items, mode = rerank.rerank(CANDIDATES, CONTEXT, NullLlmClient(), limit=2)
    assert mode == "keyword"
    assert [item["asset_id"] for item in items] == ["a1", "a2"]


def test_model_picks_and_writes_reasons() -> None:
    llm = FakeLlm({"picked": [{"asset_id": "a1", "reason": "同样是批量导入超时", "score": 9}]})
    items, mode = rerank.rerank(CANDIDATES, CONTEXT, llm, limit=3)
    assert mode == "reranked"
    assert [item["asset_id"] for item in items] == ["a1"]
    assert items[0]["reasons"][0] == "同样是批量导入超时"
    assert items[0]["score"] == 9
    # 粗排的理由保留在后面，方便对照
    assert "关键词命中 导入" in items[0]["reasons"]


def test_model_hallucination_is_ignored() -> None:
    """模型编出来的 asset_id 直接丢掉，不能凭空推荐一个不存在的资产。"""
    llm = FakeLlm({"picked": [{"asset_id": "不存在", "reason": "瞎编的"}]})
    items, mode = rerank.rerank(CANDIDATES, CONTEXT, llm, limit=3)
    assert mode == "keyword", "一条都没对上就退回粗排"
    assert [item["asset_id"] for item in items] == ["a1", "a2", "a3"]


def test_model_failure_does_not_break_recommendation() -> None:
    items, mode = rerank.rerank(CANDIDATES, CONTEXT, FakeLlm(RuntimeError("超时")), limit=2)
    assert mode == "keyword"
    assert len(items) == 2


def test_prompt_carries_context_and_candidates() -> None:
    llm = FakeLlm({"picked": []})
    rerank.rerank(CANDIDATES, CONTEXT, llm, limit=2)
    payload = json.loads(llm.prompts[0])
    assert payload["目标"]["标题"] == "保单导入超时"
    assert len(payload["候选资产"]) == 3
    # 只带摘要，不把全文塞进去，省 token
    assert set(payload["候选资产"][0]) == {"asset_id", "kind", "title", "summary"}


def test_json_in_code_fence_is_parsed() -> None:
    assert _loads('```json\n{"picked": []}\n```') == {"picked": []}


# —— 真调模型的那条：没配密钥就跳过 ——
load_env_file(Path(__file__).resolve().parents[2] / ".env.local")
LIVE = config_from_env().usable and os.environ.get("FDE_ASSET_LLM_LIVE_TEST", "1") == "1"


@pytest.mark.skipif(not LIVE, reason="没有配置模型网关（.env.local）")
def test_real_model_reranks_and_explains() -> None:
    """真连模型：相关的要挑出来，不相关的不要硬凑，理由得是人话。"""
    items, mode = rerank.rerank(CANDIDATES, CONTEXT, build_client(), limit=3)
    if mode != "reranked":
        pytest.skip("模型调用没成功（多半是网络或配额），已自动退回关键词排序")
    assert items, "至少该挑出一条"
    assert items[0]["asset_id"] == "a1", f"最相关的应该是导入超时那条，实际 {items[0]['asset_id']}"
    reason = items[0]["reasons"][0]
    assert len(reason) >= 4 and "asset_id" not in reason, f"理由要是人话：{reason}"
    assert all(item["asset_id"] in {"a1", "a2", "a3"} for item in items)
