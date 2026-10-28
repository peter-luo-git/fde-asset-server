"""沉淀候选：同一件事合并、处理过的不再冒出来、各种处理方式、红色数字、草稿删掉回到待处理。"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from fde_asset.core.db import harvest_candidates
from fde_asset.modules.harvest import suggestions
from fde_asset.platform.identity import Membership, Principal

CHEN = Principal("chen", memberships=(Membership("policy-import", "finance", "owner"),))
WANG = Principal("wang", memberships=(Membership("policy-import", "finance", "member"),))
READER = Principal("li", memberships=(Membership("policy-import", "finance", "viewer"),))


def _suggest(engine, *, scope="channel", scope_id="ch-1", owner="chen", title="导入超时排查", **kw):
    return suggestions.record(
        engine,
        engagement_slug="policy-import",
        scope=scope,
        scope_id=scope_id,
        owner_user=owner,
        source=kw.pop("source", "agent"),
        created_by=owner,
        kind=kw.pop("kind", "Case"),
        title=title,
        **kw,
    )


def test_same_thing_merges_and_handled_ones_do_not_come_back(context) -> None:
    engine = context.engine
    first = _suggest(engine, files=["a.md"])
    again = _suggest(engine, scope_id="ch-2", title="导入超时 排查", files=["b.md"])
    assert again["id"] == first["id"] and again["outcome"] == "merged"
    assert again["files"] == ["a.md", "b.md"]
    assert suggestions.counts(engine, WANG) == {"total": 1, "by_project": {"policy-import": 1}}

    suggestions.decide(engine, WANG, first["id"], "ignored", note="一次性问题")
    third = _suggest(engine, source="message")
    assert third["id"] == first["id"] and third["outcome"] == "already_handled"
    assert third["status"] == "ignored" and third["mentions"] == 1
    assert suggestions.counts(engine, WANG)["total"] == 0


def test_dm_suggestions_stay_private_and_do_not_merge_into_channel(context) -> None:
    engine = context.engine
    channel = _suggest(engine)
    private = _suggest(engine, scope="dm", scope_id="dm-1", owner="wang", files=["secret.md"])
    assert private["id"] != channel["id"]
    assert [item["id"] for item in suggestions.list_for_project(engine, CHEN, "policy-import")] == [
        channel["id"]
    ]
    assert suggestions.counts(engine, WANG)["total"] == 2
    # 私聊的并进频道候选：只记一次提到，文件不过去
    suggestions.decide(engine, WANG, private["id"], "duplicate", merged_into=channel["id"])
    target = suggestions.list_for_project(engine, CHEN, "policy-import", "open")[0]
    assert target["files"] == [] and target["mentions"] == 1


def test_hold_link_and_read_only_rules(context) -> None:
    engine = context.engine
    item = _suggest(engine)
    with pytest.raises(suggestions.SuggestionError) as denied:
        suggestions.decide(engine, READER, item["id"], "ignored")
    assert denied.value.status == 403
    held = suggestions.decide(engine, WANG, item["id"], "hold")
    assert held["status"] == "hold"
    assert suggestions.counts(engine, CHEN)["total"] == 0  # 待定不计入
    with pytest.raises(suggestions.SuggestionError):
        suggestions.decide(engine, WANG, item["id"], "linked")
    linked = suggestions.decide(engine, WANG, item["id"], "linked", asset_id="asset-1")
    assert linked["related_asset_id"] == "asset-1" and linked["decided_by"] == "wang"
    reopened = suggestions.decide(engine, CHEN, item["id"], "open")
    assert reopened["status"] == "open" and reopened["related_asset_id"] == ""


def test_drafted_follows_the_draft_and_returns_when_draft_is_gone(context) -> None:
    engine = context.engine
    item = _suggest(engine)
    with engine.begin() as conn:
        conn.execute(
            harvest_candidates.insert().values(
                candidate_id="cand-1", kind="Case", name="import-timeout", status="submitted"
            )
        )
    suggestions.decide(engine, WANG, item["id"], "drafted", "cand-1")
    listed = suggestions.list_for_project(engine, CHEN, "policy-import", "drafted")[0]
    assert listed["draft_label"] == "评审中"
    with engine.begin() as conn:
        conn.execute(
            update(harvest_candidates)
            .where(harvest_candidates.c.candidate_id == "cand-1")
            .values(status="merged")
        )
    listed = suggestions.list_for_project(engine, CHEN, "policy-import", "drafted")[0]
    assert listed["draft_label"] == "已沉淀" and listed["asset_id"]
    with engine.begin() as conn:
        conn.execute(harvest_candidates.delete())
    assert suggestions.list_for_project(engine, CHEN, "policy-import", "drafted") == []
    assert suggestions.list_for_project(engine, CHEN, "policy-import", "open")[0]["id"] == item["id"]
