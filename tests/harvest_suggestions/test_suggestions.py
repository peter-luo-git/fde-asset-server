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
    again = _suggest(engine, title="导入超时 排查", files=["b.md"])
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
    assert (
        suggestions.list_for_project(engine, CHEN, "policy-import", "open")[0]["id"] == item["id"]
    )


def test_handled_channel_does_not_receive_private_message_metadata(context) -> None:
    engine = context.engine
    channel = _suggest(engine)
    suggestions.decide(engine, WANG, channel["id"], "ignored")
    private = _suggest(
        engine,
        scope="dm",
        scope_id="dm-private",
        owner="wang",
        conversation_id="private-conversation",
        message_id="private-message",
        summary="private detail",
        files=["secret.md"],
    )
    assert private["id"] != channel["id"]
    public = suggestions.list_for_project(engine, CHEN, "policy-import")[0]
    assert public["mentions"] == 0 and public["mentions_list"] == []


def test_files_remain_bound_to_the_original_conversation(context) -> None:
    engine = context.engine
    original = _suggest(engine, conversation_id="conv-a", files=["a.md"])
    merged = _suggest(engine, scope_id="ch-2", conversation_id="conv-b", files=["b.md"])
    assert merged["id"] == original["id"]
    assert merged["scope_id"] == "ch-1"
    assert merged["conversation_id"] == "conv-a"
    assert merged["files"] == ["a.md"]


@pytest.mark.parametrize("scope", ["channel", "dm"])
def test_read_only_member_cannot_mark_a_message(client, scope) -> None:
    context = client.app.state.context
    data = context.directory._load()
    data["users"]["chen"]["memberships"][0]["role"] = "viewer"
    context.directory.write(data)
    client.headers.update({"X-FDE-User": "chen"})
    response = client.post(
        "/api/v1/harvest-suggestions",
        json={
            "engagement_slug": "policy-import",
            "scope": scope,
            "scope_id": "ch-1",
            "kind": "Case",
            "title": "readonly",
        },
    )
    assert response.status_code == 403, response.text
    assert suggestions.counts(context.engine, CHEN)["total"] == 0


def test_private_suggestion_cannot_be_mutated_after_project_access_is_revoked(context) -> None:
    item = _suggest(context.engine, scope="dm", scope_id="dm-1", owner="wang")
    revoked = Principal("wang")
    with pytest.raises(suggestions.SuggestionError) as denied:
        suggestions.decide(context.engine, revoked, item["id"], "ignored")
    assert denied.value.status in {403, 404}


def test_repeated_duplicate_decision_does_not_recount_or_move_foreign_files(context) -> None:
    engine = context.engine
    first = _suggest(engine, title="first", conversation_id="conv-a", files=["a.md"])
    second = _suggest(
        engine, title="second", scope_id="ch-2", conversation_id="conv-b", files=["b.md"]
    )
    for _ in range(2):
        suggestions.decide(engine, WANG, second["id"], "duplicate", merged_into=first["id"])
    target = next(
        item
        for item in suggestions.list_for_project(engine, CHEN, "policy-import")
        if item["id"] == first["id"]
    )
    assert target["files"] == ["a.md"]
    assert target["mentions"] == 1


def test_mark_checks_real_source_and_cannot_forge_another_private_conversation(client) -> None:
    context = client.app.state.context
    source = {
        "username": "chen",
        "engagement_slug": "policy-import",
        "scope": "dm",
        "scope_id": "dm-owned",
        "conversation_id": "dm-owned",
        "message_id": "msg-1",
        "agent_profile_id": "pa-chen",
    }
    data = context.directory._load()
    data["harvest_sources"] = [source]
    context.directory.write(data)
    client.headers.update({"X-FDE-User": "chen"})
    payload = {**source, "kind": "Case", "title": "verified"}
    good = client.post("/api/v1/harvest-suggestions", json=payload)
    assert good.status_code == 200, good.text
    forged = client.post(
        "/api/v1/harvest-suggestions",
        json={
            **payload,
            "scope_id": "dm-other",
            "conversation_id": "dm-other",
            "title": "forged",
        },
    )
    assert forged.status_code == 404, forged.text
    assert suggestions.counts(context.engine, CHEN)["total"] == 1


def test_browser_mark_requires_a_real_message_id(client) -> None:
    client.headers.update({"X-FDE-User": "chen"})
    response = client.post(
        "/api/v1/harvest-suggestions",
        json={
            "engagement_slug": "policy-import",
            "scope": "channel",
            "scope_id": "ch-1",
            "kind": "Case",
            "title": "missing message",
        },
    )
    assert response.status_code == 422, response.text


def test_suggestion_files_reject_absolute_and_escaping_paths(context) -> None:
    item = _suggest(
        context.engine,
        files=[
            "/absolute.md",
            "../outside.md",
            "safe/../outside.md",
            "..\\outside.md",
            "nul\x00.md",
            ".git/config",
            "safe/answer.md",
        ],
    )
    assert item["files"] == ["safe/answer.md"]


def test_agent_suggestion_revalidates_its_conversation_and_uses_verified_agent(seeded) -> None:
    from tests.platform.test_agent_integration import HEADERS, _service

    with _service(seeded) as client:
        context = client.app.state.context
        source = {
            "username": "chen",
            "engagement_slug": "policy-import",
            "scope": "channel",
            "scope_id": "ch-1",
            "conversation_id": "conv-1",
            "message_id": "",
            "agent_profile_id": "pa-verified",
        }
        data = context.directory._load()
        data["harvest_sources"] = [source]
        context.directory.write(data)
        payload = {**source, "agent": "forged-agent", "kind": "Case", "title": "agent source"}
        path = "/internal/v1/harvest-suggestions"
        assert client.post(path, json=payload).status_code == 404
        accepted = client.post(path, json=payload, headers=HEADERS)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["created_by"] == "pa-verified"
        denied = client.post(path, json={**payload, "conversation_id": "other"}, headers=HEADERS)
        assert denied.status_code == 404, denied.text


@pytest.mark.parametrize("action", ["mark", "decide"])
def test_suggestion_writes_refresh_platform_membership_after_downgrade(client, action) -> None:
    from fde_asset.api.deps import get_principal
    from tests.platform.test_platform_identity import DIRECTORY, WANG, FakePlatform, directory

    context = client.app.state.context
    platform = FakePlatform({})
    context.directory = directory(platform)
    stale = context.directory.resolve("wang")
    platform.directory_payload = {
        **DIRECTORY,
        "users": [
            {
                **WANG,
                "memberships": [{**WANG["memberships"][0], "role": "viewer"}],
            }
        ],
    }
    client.app.dependency_overrides[get_principal] = lambda: stale
    if action == "mark":
        response = client.post(
            "/api/v1/harvest-suggestions",
            json={
                "engagement_slug": "policy-import",
                "scope": "channel",
                "scope_id": "ch-1",
                "message_id": "msg-1",
                "kind": "Case",
                "title": "after downgrade",
            },
        )
    else:
        item = _suggest(context.engine)
        response = client.post(
            f"/api/v1/harvest-suggestions/{item['id']}/decide", json={"status": "ignored"}
        )
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("decision", ["drafted", "linked"])
def test_decision_cannot_bind_an_unreadable_draft_or_asset(client, decision) -> None:
    from sqlalchemy import select
    from fde_asset.core.db import assets

    context = client.app.state.context
    item = _suggest(context.engine)
    with context.engine.begin() as conn:
        if decision == "drafted":
            conn.execute(
                harvest_candidates.insert().values(
                    candidate_id="private-candidate",
                    kind="Case",
                    name="private-candidate",
                    created_by="wang",
                    status="draft",
                )
            )
            payload = {"status": decision, "candidate_id": "private-candidate"}
            actor, expected = "chen", 403
        else:
            hidden = conn.execute(
                select(assets.c.asset_id).where(assets.c.name == "pg-vacuum-tuning")
            ).scalar_one()
            payload = {"status": decision, "asset_id": hidden}
            actor, expected = "wang", 404
    client.headers.update({"X-FDE-User": actor})
    response = client.post(f"/api/v1/harvest-suggestions/{item['id']}/decide", json=payload)
    assert response.status_code == expected, response.text
    assert (
        suggestions.list_for_project(context.engine, CHEN, "policy-import")[0]["status"] == "open"
    )
