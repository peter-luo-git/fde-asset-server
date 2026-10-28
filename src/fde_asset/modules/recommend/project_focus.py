"""项目近期在问什么（2026-10-28）：精确推荐在项目信息之外，再加上项目里用户问 Agent 的问题的总结。

项目的标题和描述往往是立项时写的，过一段时间就和实际在做的事对不上；
频道里大家问 Agent 的问题最能反映项目现在在忙什么（比如在查 MySQL 慢查询、在写生产变更手册）。

- 问题来源：平台给的项目频道、话题里最近 30 天用户问 Agent 的问题（私聊只有本人能看，不用）；
- 总结：交给 Agent 用的同一套模型，写成一两句「近期在问」和几个关键词；没配模型就把最近几个问题直接拼起来；
- 缓存：同一批问题只总结一次（按问题内容算指纹），最多留 6 小时；
- 问不到、模型挂了都返回空，推荐照常只按项目信息算。
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any

SYSTEM = (
    "你在帮资产中心理解一个项目近期在做什么。下面是项目频道里用户最近问 Agent 的问题（新的在前）。"
    "用中文总结：summary 写一两句话，说这个项目近期主要在处理哪些事、遇到什么问题（不超过 120 字，不要罗列每个问题，"
    "不要写人名）；keywords 给 3~8 个最能代表这些事的技术或业务关键词。"
    '只输出 JSON：{"summary": "...", "keywords": ["..."]}'
)

MAX_QUESTIONS = 40
TTL_SECONDS = 6 * 3600
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()

_MENTION = re.compile(r"@\S+")


def _clean(question: str) -> str:
    """去掉 @某人、多余空白，截短：总结看的是在问什么，不是谁问的。"""
    text = _MENTION.sub("", question)
    return " ".join(text.split())[:200]


def summarize(questions: list[str], llm: Any = None) -> dict[str, Any]:
    """返回 {"summary": 一两句话, "keywords": [...], "questions": 用了几条}；没有问题返回空 summary。"""
    cleaned = [text for text in (_clean(item) for item in questions) if len(text) >= 4][
        :MAX_QUESTIONS
    ]
    if not cleaned:
        return {"summary": "", "keywords": [], "questions": 0}
    fingerprint = hashlib.sha256("\n".join(cleaned).encode()).hexdigest()
    now = time.monotonic()
    with _lock:
        hit = _cache.get(fingerprint)
        if hit and now - hit[0] < TTL_SECONDS:
            return hit[1]
    result: dict[str, Any] | None = None
    if llm is not None and getattr(llm, "usable", False):
        try:
            payload = llm.complete_json(
                SYSTEM, "\n".join(f"{index + 1}. {text}" for index, text in enumerate(cleaned))
            )
            summary = str(payload.get("summary") or "").strip()[:200]
            keywords = [
                str(item).strip()[:30]
                for item in (payload.get("keywords") or [])
                if str(item).strip()
            ][:8]
            if summary:
                result = {"summary": summary, "keywords": keywords, "questions": len(cleaned)}
        except Exception:  # noqa: BLE001 - 总结不出来就用原问题，推荐不能因此失败
            result = None
    if result is None:
        # 没有模型：最近几个问题直接拼起来，重排模型照样能看懂
        result = {
            "summary": "；".join(cleaned[:5])[:300],
            "keywords": [],
            "questions": len(cleaned),
        }
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[fingerprint] = (now, result)
    return result


def for_project(
    directory: Any, engagement_slug: str, llm: Any = None, agent: str = ""
) -> dict[str, Any]:
    """取项目近期问题并总结。给了 agent 就只看项目里问这个 Agent 的（为 Agent 找资产）；
    项目找资产看问所有 Agent 的。目录不支持（老版本）或问不到就返回空。"""
    fetch = getattr(directory, "recent_questions", None)
    if not engagement_slug or fetch is None:
        return {"summary": "", "keywords": [], "questions": 0}
    try:
        questions = fetch(engagement_slug, agent) if agent else fetch(engagement_slug)
    except Exception:  # noqa: BLE001 - 平台问不到就不加
        return {"summary": "", "keywords": [], "questions": 0}
    result = summarize(questions, llm)
    return {**result, "agent": agent}
