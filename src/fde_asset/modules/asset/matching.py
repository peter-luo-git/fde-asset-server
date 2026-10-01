"""资产匹配：给一个项目或 Agent 的上下文，算出该带哪些资产。

这是"使用"这一环的入口。四类分发机制各走各的规则（见实现方案 §3.1）：

| 组 | 机制 | 怎么选 |
|---|---|---|
| rules | 强制注入 | 可见范围内的规范**全要**，不做相关性筛选——规范是红线，不能因为"不相关"被漏掉 |
| sops | 流程注入 | 按行业与关键词选最贴的一条主流程 |
| skills | 工具发现 | 关键词命中即挂载，多挂几个成本很低 |
| knowledge | 文档检索 | 方案/实施/问题/经验按相关性排序，取前 N 条 |

评分故意做得简单可解释：每一条命中都会写进 reasons，页面上直接显示"为什么推荐它"。
不做向量检索——v0.1 的资产量级（几十到几百）用关键词加权足够，而且可解释性更重要。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets
from fde_asset.modules.asset.catalog import reuse_counts
from fde_asset.modules.asset.visibility import visibility_clause
from fde_asset.platform.identity import Principal

#: 知识类资产最多推几条，避免把提示词预算吃光
KNOWLEDGE_LIMIT = 6
SKILL_LIMIT = 8
SOP_LIMIT = 3

_WORD = re.compile(r"[A-Za-z0-9_]+")
_STOP = {"的", "了", "和", "与", "及", "在", "是", "做", "一个", "我们", "这个", "进行", "相关"}


@dataclass
class MatchContext:
    """一次匹配的输入：项目信息 + 当前要做的事 + Agent 角色。"""

    title: str = ""
    description: str = ""
    industry: str = ""
    department_code: str = ""
    engagement_slug: str = ""
    agent_role: str = ""
    stage: str = ""
    keywords: list[str] = field(default_factory=list)


def tokenize(text: str) -> set[str]:
    """中英混排的轻量切词：英文按单词，中文按二字窗口。"""
    if not text:
        return set()
    tokens = {match.group(0).lower() for match in _WORD.finditer(text) if len(match.group(0)) > 1}
    chinese = re.sub(r"[^一-鿿]+", " ", text)
    for run in chinese.split():
        for size in (2, 3):
            for index in range(len(run) - size + 1):
                piece = run[index : index + size]
                if piece not in _STOP:
                    tokens.add(piece)
    return tokens


def _haystack(row: Any) -> str:
    tags = " ".join(json.loads(row.tags_json or "[]"))
    return f"{row.title} {row.summary} {row.name} {tags}".lower()


def _score(
    row: Any, context: MatchContext, tokens: set[str], reuse: int
) -> tuple[float, float, list[str]]:
    """返回（相关度, 总分, 理由）。

    相关度只看内容信号（行业、关键词）；作用域、复用、等级只做加权。
    一条完全不沾边的资产不能仅凭「本部门」就被推出来——否则部门里所有资产都会被推。
    """
    reasons: list[str] = []
    relevance = 0.0
    score = 0.0

    industries = json.loads(row.industry_json or "[]")
    if context.industry and context.industry in industries:
        relevance += 3
        reasons.append(f"行业命中 {context.industry}")

    haystack = _haystack(row)
    hits = sorted({token for token in tokens if token and token in haystack})
    if hits:
        relevance += min(len(hits), 4) * 1.5
        reasons.append("关键词命中 " + "、".join(hits[:4]))

    if row.scope == "engagement" and row.engagement_slug == context.engagement_slug:
        score += 2
        reasons.append("本项目沉淀的资产")
    elif row.scope == "department" and row.department_code == context.department_code:
        score += 1
        reasons.append("本部门资产")

    if reuse:
        score += min(reuse, 3) * 0.5
        reasons.append(f"已被 {reuse} 个项目复用")

    if row.quality == "gold":
        score += 1
        reasons.append("金级")
    elif row.quality == "silver":
        score += 0.5
        reasons.append("银级")

    return relevance, relevance + score, reasons


def _item(row: Any, score: float, reasons: list[str], reuse: int) -> dict[str, Any]:
    return {
        "asset_id": row.asset_id,
        "kind": row.kind,
        "name": row.name,
        "title": row.title,
        "summary": row.summary,
        "ref": f"[[{row.kind.lower()}/{row.name}]]",
        "scope": row.scope,
        "department_code": row.department_code,
        "engagement_slug": row.engagement_slug,
        "owner_ref": row.owner_ref,
        "quality": row.quality,
        "lifecycle": row.lifecycle,
        "reuse_engagement_count": reuse,
        "score": round(score, 2),
        "reasons": reasons,
    }


def match(engine: Engine, principal: Principal, context: MatchContext) -> dict[str, Any]:
    """返回四组推荐，每组按得分从高到低。"""
    tokens = tokenize(f"{context.title} {context.description} {context.stage}")
    tokens.update(token.lower() for token in context.keywords if token)

    with engine.connect() as conn:
        rows = conn.execute(
            select(assets).where(
                visibility_clause(principal),
                assets.c.valid.is_(True),
                assets.c.lifecycle.notin_(["deprecated", "archived", "draft"]),
            )
        ).fetchall()
        reuse_map = reuse_counts(conn)

    buckets: dict[str, list[dict[str, Any]]] = {
        "rules": [],
        "sops": [],
        "skills": [],
        "knowledge": [],
    }
    for row in rows:
        reuse = reuse_map.get(row.asset_id, 0)
        relevance, score, reasons = _score(row, context, tokens, reuse)
        if row.kind == "Rule":
            # 规范一律带上：红线不挑场景
            buckets["rules"].append(_item(row, score + 10, ["规范强制注入"] + reasons, reuse))
        elif row.kind == "Sop":
            # 流程没命中关键词也给一条兜底，免得新项目完全没有流程可依
            buckets["sops"].append(_item(row, score, reasons or ["通用流程"], reuse))
        elif relevance > 0:
            bucket = "skills" if row.kind == "Skill" else "knowledge"
            buckets[bucket].append(_item(row, score, reasons, reuse))

    for key in buckets:
        buckets[key].sort(key=lambda item: item["score"], reverse=True)
    buckets["sops"] = buckets["sops"][:SOP_LIMIT]
    buckets["skills"] = buckets["skills"][:SKILL_LIMIT]
    buckets["knowledge"] = buckets["knowledge"][:KNOWLEDGE_LIMIT]

    return {
        "context": {
            "title": context.title,
            "industry": context.industry,
            "department_code": context.department_code,
            "engagement_slug": context.engagement_slug,
            "agent_role": context.agent_role,
            "stage": context.stage,
            "tokens": sorted(tokens)[:20],
        },
        **buckets,
        "summary": {
            "rules": len(buckets["rules"]),
            "sops": len(buckets["sops"]),
            "skills": len(buckets["skills"]),
            "knowledge": len(buckets["knowledge"]),
        },
    }
