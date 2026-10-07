"""评审相关的事同步到平台工作台的待办：谁该看到、什么时候收掉。"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from fde_asset.api.app import create_app
from fde_asset.platform.platform_inbox import NoPlatformInbox, PlatformInbox
from tests.conftest import as_user
from tests.platform.test_customer_drafts import BODY


class Recorder:
    """记下资产中心想往平台待办里放什么、收什么。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def open(self, usernames, **kwargs) -> None:
        self.calls.append(
            ("open", sorted(usernames), kwargs["candidate_id"], kwargs["event"], kwargs["title"])
        )

    def resolve(self, **kwargs) -> None:
        self.calls.append(
            (
                "resolve",
                sorted(kwargs.get("usernames") or []),
                kwargs["candidate_id"],
                kwargs.get("event", ""),
            )
        )


@pytest.fixture()
def recorded(seeded):
    app = create_app(seeded)
    with TestClient(app) as client:
        recorder = Recorder()
        app.state.context.platform_inbox = recorder
        client.headers.update({"X-FDE-User": "admin"})
        yield client, recorder


def _draft_and_submit(client) -> tuple[str, str]:
    chen = as_user(client, "chen")
    candidate_id = chen.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Solution",
            "title": "对账口径",
            "scope": "engagement",
            "engagement_slug": "policy-import",
        },
    ).json()["candidate_id"]
    chen.patch(
        f"/api/v1/harvest-candidates/{candidate_id}",
        json={
            "files": {"README.md": BODY},
            "meta": {
                "summary": "对账以保单号加批次为键",
                "suitable": "保单批量导入",
                "notSuitable": "实时接口",
            },
        },
    )
    submitted = chen.post(f"/api/v1/harvest-candidates/{candidate_id}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    return candidate_id, submitted.json()["review_id"]


def test_submit_opens_an_item_for_reviewers_not_for_the_submitter(recorded) -> None:
    client, recorder = recorded
    candidate_id, review_id = _draft_and_submit(client)

    opened = [call for call in recorder.calls if call[0] == "open"]
    assert len(opened) == 1
    _, reviewers, candidate, event, title = opened[0]
    assert candidate == candidate_id and event == f"review:{review_id}" and "对账口径" in title
    assert "wang" in reviewers and "chen" not in reviewers
    # 重新提交时，提交人之前那条"被打回"要收掉
    assert ("resolve", ["chen"], candidate_id, "result") in recorder.calls


def test_decision_resolves_reviewers_and_tells_the_submitter(recorded) -> None:
    client, recorder = recorded
    candidate_id, review_id = _draft_and_submit(client)
    recorder.calls.clear()

    done = as_user(client, "wang").post(
        f"/api/v1/reviews/{review_id}/decide", json={"approve": False, "note": "补现象"}
    )
    assert done.status_code == 200, done.text
    assert recorder.calls[0] == ("resolve", [], candidate_id, "review")
    kind, who, candidate, event, title = recorder.calls[1]
    assert (kind, who, candidate, event) == ("open", ["chen"], candidate_id, f"result:{review_id}")
    assert title.startswith("资产被打回：")


def test_viewing_a_merged_draft_clears_the_result_item(recorded) -> None:
    client, recorder = recorded
    candidate_id, review_id = _draft_and_submit(client)
    as_user(client, "wang").post(f"/api/v1/reviews/{review_id}/decide", json={"approve": True})
    assert any(call[0] == "open" and call[4].startswith("资产已入库：") for call in recorder.calls)
    recorder.calls.clear()

    # 别人看不算，本人看过才收掉
    as_user(client, "wang").get(f"/api/v1/harvest-candidates/{candidate_id}")
    assert recorder.calls == []
    as_user(client, "chen").get(f"/api/v1/harvest-candidates/{candidate_id}")
    assert recorder.calls == [("resolve", ["chen"], candidate_id, "result")]


def test_platform_outage_never_breaks_the_review(seeded) -> None:
    class Down:
        def post(self, *_args, **_kwargs):
            raise ConnectionError("平台连不上")

    app = create_app(seeded)
    with TestClient(app) as client:
        app.state.context.platform_inbox = PlatformInbox(
            "http://server-api:8000", "k" * 32, client=Down()
        )
        client.headers.update({"X-FDE-User": "admin"})
        candidate_id, review_id = _draft_and_submit(client)
        assert (
            as_user(client, "wang")
            .post(f"/api/v1/reviews/{review_id}/decide", json={"approve": True})
            .status_code
            == 200
        )
        assert (
            as_user(client, "chen")
            .get(f"/api/v1/harvest-candidates/{candidate_id}")
            .json()["status"]
            == "merged"
        )


def test_local_mode_has_no_platform_inbox(seeded) -> None:
    with TestClient(create_app(seeded)) as client:
        assert isinstance(client.app.state.context.platform_inbox, NoPlatformInbox)
