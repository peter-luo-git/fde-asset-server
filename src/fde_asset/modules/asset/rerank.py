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


def order_by_model(
    candidates: list[dict[str, Any]],
    query: str,
    reranker: Any,
    *,
    limit: int,
    min_score: float | None = None,
) -> list[dict[str, Any]] | None:
    """用重排模型排序。只打分不生成，比让大模型写字快一个量级。

    返回 None 表示没配或调用失败，调用方继续用粗排顺序。
    给了 `min_score` 时低于门槛的不要；全都不够格就返回空列表——宁缺毋滥，不退回粗排。
    """
    if reranker is None or not getattr(reranker, "usable", False) or not candidates:
        return None
    documents = [
        f"{item['title']}。{(item.get('summary') or '')[:MAX_SUMMARY]}" for item in candidates
    ]
    try:
        scored = reranker.rank(query, documents, limit)
    except Exception:  # noqa: BLE001 - 重排挂了不能让推荐瘫掉
        return None
    if not scored:
        return None
    ordered: list[dict[str, Any]] = []
    for row in scored[:limit]:
        if not 0 <= row.index < len(candidates):
            continue
        if min_score is not None and row.score < min_score:
            continue
        item = dict(candidates[row.index])
        item["score"] = round(row.score * 10, 2)
        item["reasons"] = [f"相关度 {row.score:.2f}", *item.get("reasons", [])]
        item["reranked"] = True
        ordered.append(item)
    if min_score is not None:
        return ordered
    return ordered or None


def rerank(
    candidates: list[dict[str, Any]],
    context: dict[str, Any],
    client: LlmClient | None,
    *,
    limit: int = 8,
    reranker: Any = None,
    reason_top_n: int = 3,
    min_score: float | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """返回（结果, 用了哪种模式）。模式是 reranked 或 keyword。"""
    if not candidates:
        return [], "keyword"

    query = "；".join(str(value) for value in context.values() if value)

    # 第一优先：重排模型排序（快），再让生成模型只给前几条写理由（省 token）
    ordered = order_by_model(candidates, query, reranker, limit=limit, min_score=min_score)
    if ordered is not None:
        if ordered and client is not None and getattr(client, "usable", False) and reason_top_n > 0:
            head, tail = ordered[:reason_top_n], ordered[reason_top_n:]
            try:
                payload = client.complete_json(SYSTEM, build_prompt(context, head, len(head)))
                explained = apply(head, payload.get("picked") or [], limit=len(head))
                if explained:
                    by_id = {item["asset_id"]: item for item in explained}
                    head = [by_id.get(item["asset_id"], item) for item in head]
            except Exception:  # noqa: BLE001 - 写不出理由就只给分数，不影响排序
                pass
            ordered = head + tail
        return ordered, "reranked"

    # 没有重排模型时，退回让生成模型直接挑
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
