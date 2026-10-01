"""内容理解重排：把关键词粗排的结果交给模型精排，并让它写出人话理由。

为什么分两步（见设计文档 §1.4b）：
- 可见性必须在 SQL 里下推，不能让模型看到无权看的资产；
- 全库几百条逐条过模型，成本和延迟都不划算；
- 所以先用结构化规则粗排 20~30 条，再让模型从中挑 6~10 条并说明为什么。

任何一步出问题都退回粗排结果，不让推荐因为模型抽风而瘫掉。
"""

from __future__ import annotations

import json
from typing import Any

from fde_asset.platform.llm.client import LlmClient

SYSTEM = (
    "你是交付团队的资产助手。给你一个项目或 Agent 的上下文，和一批候选资产的摘要，"
    "挑出真正用得上的，并为每条写一句话说明为什么用得上。"
    "判断依据是内容是否对得上，不要只看字面重合。"
    '只输出 JSON：{"picked":[{"asset_id":"...","reason":"一句话","score":0-10}]}。'
    "宁缺毋滥：不相关的不要硬凑。"
)

MAX_CANDIDATES = 30
MAX_SUMMARY = 160


def _candidate_brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": item["asset_id"],
        "kind": item["kind"],
        "title": item["title"],
        "summary": (item.get("summary") or "")[:MAX_SUMMARY],
    }


def build_prompt(context: dict[str, Any], candidates: list[dict[str, Any]], limit: int) -> str:
    return json.dumps(
        {
            "目标": context,
            "候选资产": [_candidate_brief(item) for item in candidates[:MAX_CANDIDATES]],
            "最多挑几条": limit,
        },
        ensure_ascii=False,
    )


def apply(
    candidates: list[dict[str, Any]],
    picked: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """把模型给的 asset_id 与理由贴回候选项；模型编出来的 id 直接忽略。"""
    by_id = {item["asset_id"]: item for item in candidates}
    result: list[dict[str, Any]] = []
    for choice in picked:
        item = by_id.get(str(choice.get("asset_id", "")))
        if item is None:
            continue
        reason = str(choice.get("reason", "")).strip()
        merged = dict(item)
        if reason:
            merged["reasons"] = [reason, *item.get("reasons", [])]
        try:
            merged["score"] = round(float(choice.get("score", item.get("score", 0))), 2)
        except (TypeError, ValueError):
            pass
        merged["reranked"] = True
        result.append(merged)
        if len(result) >= limit:
            break
    return result


def rerank(
    candidates: list[dict[str, Any]],
    context: dict[str, Any],
    client: LlmClient | None,
    *,
    limit: int = 8,
) -> tuple[list[dict[str, Any]], str]:
    """返回（结果, 用了哪种模式）。模式是 reranked 或 keyword。"""
    if not candidates:
        return [], "keyword"
    if client is None or not getattr(client, "usable", False):
        return candidates[:limit], "keyword"
    try:
        payload = client.complete_json(SYSTEM, build_prompt(context, candidates, limit))
        picked = payload.get("picked") or []
        chosen = apply(candidates, picked, limit=limit)
    except Exception:  # noqa: BLE001 - 模型抽风不能让推荐瘫掉
        return candidates[:limit], "keyword"
    if not chosen:
        return candidates[:limit], "keyword"
    return chosen, "reranked"
