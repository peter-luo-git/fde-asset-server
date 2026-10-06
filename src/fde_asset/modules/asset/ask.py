"""按问题检索：输入一句话，按语义找资产。

和目录的关键词筛选不是一回事：关键词要求字面出现，问「报错怎么办」找不到写着
「故障处理」的资产。这里把问题和**全部可见资产**交给重排模型逐条打分，
不先做字面粗筛——粗筛漏掉的，后面的模型再强也捞不回来。

可见性仍然在 SQL 里下推，模型看不到提问的人无权看的资产。
资产到几千条、一次调用放不下时，再在前面加一层向量召回。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.engine import Engine

from fde_asset.core.db import assets
from fde_asset.modules.asset import catalog
from fde_asset.modules.asset.matching import tokenize
from fde_asset.platform.identity import Principal

#: 一次交给重排模型的资产上限，超过就只取最近更新的这些
MAX_DOCUMENTS = 500
CONCLUSION_CHARS = 300
DEFAULT_LIMIT = 10
DEFAULT_MIN_SCORE = 0.05


def document(row: Any) -> str:
    """喂给模型的一段话：标题、摘要、结论段、标签。全文太长，结论段最能代表一份资产。"""
    tags = " ".join(json.loads(row.tags_json or "[]"))
    parts = [row.title, row.summary, (row.conclusion_md or "")[:CONCLUSION_CHARS], tags]
    return "。".join(part.strip() for part in parts if part and part.strip())


def _keyword_scores(rows: list[Any], question: str) -> list[tuple[int, float]]:
    """没有重排模型时的退路：按切词命中数排，命中不了的不出。"""
    tokens = tokenize(question)
    scored: list[tuple[int, float]] = []
    for index, row in enumerate(rows):
        haystack = f"{document(row)} {row.name} {row.content_text or ''}".lower()
        hits = sum(1 for token in tokens if token in haystack)
        if hits:
            scored.append((index, float(hits)))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def ask(
    engine: Engine,
    principal: Principal,
    question: str,
    *,
    reranker: Any = None,
    kind: str | None = None,
    scope: str | None = None,
    exclude_kinds: tuple[str, ...] = (),
    limit: int = DEFAULT_LIMIT,
    min_score: float = DEFAULT_MIN_SCORE,
) -> dict[str, Any]:
    """返回 mode（semantic / keyword）、扫了多少份、按相关度排好的资产。"""
    question = question.strip()
    statement = (
        catalog.base_select(
            principal, catalog.CatalogQuery(kind=kind, scope=scope, exclude_kinds=exclude_kinds)
        )
        .order_by(assets.c.updated_at.desc())
        .limit(MAX_DOCUMENTS + 1)
    )
    with engine.connect() as conn:
        rows = conn.execute(statement).fetchall()
        reuse_map = catalog.reuse_counts(conn)
    truncated = len(rows) > MAX_DOCUMENTS
    rows = rows[:MAX_DOCUMENTS]

    mode = "keyword"
    scored: list[tuple[int, float]] | None = None
    if question and rows and reranker is not None and getattr(reranker, "usable", False):
        try:
            ranked = reranker.rank(question, [document(row) for row in rows], limit)
            scored = [
                (item.index, item.score)
                for item in ranked
                if 0 <= item.index < len(rows) and item.score >= min_score
            ]
            mode = "semantic"
        except Exception:  # noqa: BLE001 - 模型挂了退回关键词，搜索不能跟着瘫
            scored = None
    if scored is None:
        scored = _keyword_scores(rows, question) if question else []

    items: list[dict[str, Any]] = []
    for index, score in scored[:limit]:
        row = rows[index]
        item = catalog.row_to_dict(row, reuse_map.get(row.asset_id, 0))
        item.pop("content_text", None)
        item["relevance"] = round(score, 3)
        items.append(item)
    return {
        "mode": mode,
        "question": question,
        "scanned": len(rows),
        "truncated": truncated,
        "total": len(items),
        "items": items,
    }
