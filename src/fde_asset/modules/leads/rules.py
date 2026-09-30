"""沉淀线索挖掘：从平台活动里算出「你可能该沉淀什么」。

不依赖模型：8 条规则都是可解释、可测试的判定。线索只给本人和项目 Owner 看。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from fde_asset.core.db import asset_leads, assets

PATTERN_WORDS = ("注意", "坑", "必须", "否则", "切记", "务必")


@dataclass
class WorkItemFact:
    work_item_id: str
    title: str
    engagement_slug: str
    owner_user: str
    status: str = "done"
    kind: str = "task"
    severity: str = ""
    delivered_pr: bool = False
    changed_lines: int = 0
    closed_at: str = ""
    prompt_appends: int = 0
    review_changes_requested: int = 0
    delivery_summary: str = ""
    error_signature: str = ""
    produced_asset: bool = False


@dataclass
class EngagementFact:
    engagement_slug: str
    owner_user: str
    accepted: bool = False
    accepted_at: str = ""


class ActivitySource(Protocol):
    def work_items(self) -> Iterable[WorkItemFact]: ...
    def engagements(self) -> Iterable[EngagementFact]: ...


class LocalActivitySource:
    """本地开发用：从 JSON 文件读平台活动（正式环境换成 fde-server 只读接口）。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"work_items": [], "engagements": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def work_items(self) -> list[WorkItemFact]:
        return [WorkItemFact(**item) for item in self._load().get("work_items", [])]

    def engagements(self) -> list[EngagementFact]:
        return [EngagementFact(**item) for item in self._load().get("engagements", [])]


@dataclass
class Lead:
    rule: str
    owner_user: str
    engagement_slug: str
    subject_type: str
    subject_id: str
    suggested_kind: str
    title: str
    detail: str
    score: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def lead_id(self) -> str:
        raw = f"{self.rule}|{self.subject_type}|{self.subject_id}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _age_decay(timestamp: str, half_life_days: float = 45.0) -> float:
    if not timestamp:
        return 0.6
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return 0.6
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    days = max((datetime.now(timezone.utc) - moment) / timedelta(days=1), 0.0)
    return math.pow(0.5, days / half_life_days)


def _normalize_title(title: str) -> str:
    return re.sub(r"[0-9\W_]+", " ", title).strip().lower()


def _bigrams(text: str) -> set[str]:
    """中英文通用的字符二元组；中文没有空格，按 split 分词会失效。"""
    cleaned = _normalize_title(text).replace(" ", "")
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def _similar(a: str, b: str) -> float:
    left, right = _bigrams(a), _bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def detect(source: ActivitySource, *, existing_asset_titles: Iterable[str] = ()) -> list[Lead]:
    items = list(source.work_items())
    engagements = list(source.engagements())
    titles = list(existing_asset_titles)
    leads: list[Lead] = []

    def dedup_penalty(title: str) -> float:
        best = max((_similar(title, t) for t in titles), default=0.0)
        return max(0.0, 1.0 - best)

    for item in items:
        if (
            item.status == "done"
            and item.delivered_pr
            and item.changed_lines >= 50
            and not item.produced_asset
        ):
            leads.append(
                Lead(
                    "L1",
                    item.owner_user,
                    item.engagement_slug,
                    "work_item",
                    item.work_item_id,
                    "Skill",
                    f"「{item.title}」完成了但没有沉淀",
                    f"改动 {item.changed_lines} 行并交付了 PR，考虑沉淀成技能或实施手册",
                    score=1.0 * _age_decay(item.closed_at) * dedup_penalty(item.title),
                )
            )
    for item in items:
        if (
            item.kind == "issue"
            and item.severity in {"S1", "S2"}
            and item.status in {"closed", "done"}
            and not item.produced_asset
        ):
            leads.append(
                Lead(
                    "L2",
                    item.owner_user,
                    item.engagement_slug,
                    "work_item",
                    item.work_item_id,
                    "Case",
                    f"{item.severity} 问题单「{item.title}」没有问题资产",
                    "高严重度问题必须留下根因与如何避免",
                    score=1.4 * _age_decay(item.closed_at) * dedup_penalty(item.title),
                )
            )
    by_user: dict[str, list[WorkItemFact]] = {}
    for item in items:
        by_user.setdefault(item.owner_user, []).append(item)
    for user, user_items in by_user.items():
        used: set[str] = set()
        for index, item in enumerate(user_items):
            if item.work_item_id in used:
                continue
            group = [
                other for other in user_items[index:] if _similar(item.title, other.title) >= 0.25
            ]
            if len(group) >= 3:
                used.update(other.work_item_id for other in group)
                leads.append(
                    Lead(
                        "L3",
                        user,
                        item.engagement_slug,
                        "pattern",
                        f"{user}:{_normalize_title(item.title)[:32]}",
                        "Sop",
                        f"你做过 {len(group)} 次类似的「{item.title}」",
                        "三次法则：该固化成 SOP 或技能了",
                        score=1.6 * dedup_penalty(item.title),
                        extra={"work_items": [o.work_item_id for o in group]},
                    )
                )
    for item in items:
        if item.prompt_appends >= 4:
            leads.append(
                Lead(
                    "L4",
                    item.owner_user,
                    item.engagement_slug,
                    "work_item",
                    item.work_item_id,
                    "Skill",
                    f"「{item.title}」人工补充了 {item.prompt_appends} 次",
                    "补充越多说明隐性知识越多，值得写成技能或规范",
                    score=0.8 * _age_decay(item.closed_at) * dedup_penalty(item.title),
                )
            )
    for item in items:
        if item.review_changes_requested >= 2:
            leads.append(
                Lead(
                    "L5",
                    item.owner_user,
                    item.engagement_slug,
                    "work_item",
                    item.work_item_id,
                    "Case",
                    f"「{item.title}」被打回 {item.review_changes_requested} 次",
                    "把打回理由记成判断类问题资产",
                    score=1.2 * _age_decay(item.closed_at) * dedup_penalty(item.title),
                )
            )
    by_error: dict[str, list[WorkItemFact]] = {}
    for item in items:
        if item.error_signature:
            by_error.setdefault(item.error_signature, []).append(item)
    for signature, group in by_error.items():
        if len(group) >= 2:
            first = group[0]
            leads.append(
                Lead(
                    "L6",
                    first.owner_user,
                    first.engagement_slug,
                    "error",
                    signature,
                    "Case",
                    f"同一个错误出现了 {len(group)} 次：{signature[:40]}",
                    "重复出现的报错最值得写成问题资产",
                    score=1.3 * dedup_penalty(signature),
                    extra={"work_items": [o.work_item_id for o in group]},
                )
            )
    for item in items:
        hits = [word for word in PATTERN_WORDS if word in item.delivery_summary]
        if len(hits) >= 2:
            leads.append(
                Lead(
                    "L7",
                    item.owner_user,
                    item.engagement_slug,
                    "work_item",
                    item.work_item_id,
                    "Case",
                    f"「{item.title}」的交付摘要里有提醒",
                    f"出现了 {'、'.join(hits)}，可能是值得留下的经验",
                    score=0.6 * _age_decay(item.closed_at) * dedup_penalty(item.title),
                )
            )
    for engagement in engagements:
        if engagement.accepted:
            leads.append(
                Lead(
                    "L8",
                    engagement.owner_user,
                    engagement.engagement_slug,
                    "engagement",
                    engagement.engagement_slug,
                    "Experience",
                    f"项目 {engagement.engagement_slug} 已验收，三件套还没齐",
                    "方案 / 实施 / 复盘：验收后 15 个工作日内补齐",
                    score=1.1 * _age_decay(engagement.accepted_at),
                )
            )
    return [lead for lead in leads if lead.score >= 0.2]


def refresh(engine: Engine, source: ActivitySource) -> dict[str, Any]:
    with engine.connect() as conn:
        titles = (
            conn.execute(select(assets.c.title).where(assets.c.deleted_at.is_(None)))
            .scalars()
            .all()
        )
    leads = detect(source, existing_asset_titles=titles)
    created, updated = 0, 0
    with engine.begin() as conn:
        for lead in leads:
            exists = conn.execute(
                select(asset_leads.c.lead_id, asset_leads.c.status).where(
                    asset_leads.c.lead_id == lead.lead_id
                )
            ).first()
            if exists:
                if exists.status == "open":
                    conn.execute(
                        update(asset_leads)
                        .where(asset_leads.c.lead_id == lead.lead_id)
                        .values(score=round(lead.score, 3), title=lead.title, detail=lead.detail)
                    )
                    updated += 1
                continue
            conn.execute(
                asset_leads.insert().values(
                    lead_id=lead.lead_id,
                    rule=lead.rule,
                    owner_user=lead.owner_user,
                    engagement_slug=lead.engagement_slug,
                    subject_type=lead.subject_type,
                    subject_id=lead.subject_id,
                    suggested_kind=lead.suggested_kind,
                    title=lead.title,
                    detail=json.dumps({"detail": lead.detail, **lead.extra}, ensure_ascii=False),
                    score=round(lead.score, 3),
                    status="open",
                )
            )
            created += 1
    return {"detected": len(leads), "created": created, "updated": updated}
