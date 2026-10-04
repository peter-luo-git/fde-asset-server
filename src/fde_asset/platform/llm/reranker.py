"""重排模型客户端：只打分不生成，所以比让大模型写一堆字快一个量级。

接口按通用的 `POST /rerank` 形状实现（Jina / Cohere / BGE 系列都是这个样子）：

    请求 {"model": "...", "query": "...", "documents": ["...", ...], "top_n": 8}
    返回 {"results": [{"index": 0, "relevance_score": 0.93}, ...]}

供应商可以和写理由用的生成模型不是同一家，所以地址、密钥、模型名都单独配。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fde_asset.platform.llm.client import LlmConfig, rerank_config


@dataclass
class Scored:
    index: int
    score: float


class Reranker(Protocol):
    usable: bool

    def rank(self, query: str, documents: list[str], top_n: int) -> list[Scored]: ...


class NullReranker:
    usable = False

    def rank(self, query: str, documents: list[str], top_n: int) -> list[Scored]:
        return []


class HttpReranker:
    def __init__(self, config: LlmConfig) -> None:
        self.config = config

    @property
    def usable(self) -> bool:
        return self.config.usable

    def rank(self, query: str, documents: list[str], top_n: int) -> list[Scored]:
        import httpx

        response = httpx.post(
            rerank_url(self.config.base_url),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model,
                "query": query,
                "documents": documents,
                "top_n": min(top_n, len(documents)),
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return parse_results(response.json())


def rerank_url(base_url: str) -> str:
    """配置里写到 `/v1` 或直接写到 `/v1/rerank` 都认，免得拼出 `/rerank/rerank`。"""
    base = base_url.rstrip("/")
    return base if base.endswith("/rerank") else f"{base}/rerank"


def parse_results(payload: dict[str, Any]) -> list[Scored]:
    """容忍几种常见的返回写法：results / data，score 字段名也不统一。"""
    rows = payload.get("results") or payload.get("data") or []
    scored: list[Scored] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        index = row.get("index", row.get("document_index"))
        if index is None:
            continue
        score = row.get("relevance_score", row.get("score", 0.0))
        try:
            scored.append(Scored(index=int(index), score=float(score)))
        except (TypeError, ValueError):
            continue
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored


def build_reranker() -> Reranker:
    config = rerank_config()
    return HttpReranker(config) if config.usable else NullReranker()
