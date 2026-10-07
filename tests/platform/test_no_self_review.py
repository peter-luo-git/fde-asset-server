"""评审要换一个人看：自己提交的资产，本人有评审权也不能自己通过。"""

from __future__ import annotations

from tests.conftest import as_user
from tests.platform.test_customer_drafts import BODY


def _submit_as_project_owner(client) -> tuple[str, str]:
    """老王是 policy-import 的负责人，项目级资产本来该他评；这次是他自己提交的。"""
    wang = as_user(client, "wang")
    created = wang.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Solution",
            "title": "保单导入对账口径",
            "scope": "engagement",
            "engagement_slug": "policy-import",
        },
    )
    assert created.status_code == 200, created.text
    candidate_id = created.json()["candidate_id"]
    wang.patch(
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
    submitted = wang.post(f"/api/v1/harvest-candidates/{candidate_id}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    return candidate_id, submitted.json()["review_id"]


def test_submitter_cannot_decide_their_own_review(client) -> None:
    candidate_id, review_id = _submit_as_project_owner(client)

    refused = as_user(client, "wang").post(
        f"/api/v1/reviews/{review_id}/decide", json={"approve": True}
    )
    assert refused.status_code == 403 and "自己提交" in refused.json()["detail"]
    still = as_user(client, "wang").get(f"/api/v1/harvest-candidates/{candidate_id}")
    assert still.json()["status"] == "submitted"


def test_submitter_is_not_offered_the_review_buttons(client) -> None:
    candidate_id, review_id = _submit_as_project_owner(client)

    own_view = as_user(client, "wang").get(f"/api/v1/harvest-candidates/{candidate_id}/review")
    assert own_view.json()["can_review_myself"] is False
    assert "wang" not in {r["user_id"] for r in own_view.json()["reviewers"]}

    queue = as_user(client, "wang").get("/api/v1/reviews", params={"status": "pending"})
    mine = [item for item in queue.json()["items"] if item["review_id"] == review_id]
    assert all(item["can_decide"] is False for item in mine)


def test_another_reviewer_can_still_decide(client) -> None:
    candidate_id, review_id = _submit_as_project_owner(client)

    admin_view = as_user(client, "admin").get(f"/api/v1/harvest-candidates/{candidate_id}/review")
    assert admin_view.json()["can_review_myself"] is True
    done = as_user(client, "admin").post(
        f"/api/v1/reviews/{review_id}/decide", json={"approve": True}
    )
    assert done.status_code == 200, done.text


def test_only_project_members_can_draft_into_a_project(client) -> None:
    """不在项目里的人不能往这个项目的仓库里写东西，和客户级是一个道理。"""
    body = {
        "kind": "Case",
        "title": "外人起草",
        "scope": "engagement",
        "engagement_slug": "policy-import",
    }
    outsider = as_user(client, "zhao").post("/api/v1/harvest-candidates", json=body)
    assert (
        outsider.status_code == 422 and "不是项目 policy-import 的成员" in outsider.json()["detail"]
    )

    member = as_user(client, "chen").post("/api/v1/harvest-candidates", json=body)
    assert member.status_code == 200, member.text
    # 管理员可以代为起草
    admin = as_user(client, "admin").post("/api/v1/harvest-candidates", json=body)
    assert admin.status_code == 200, admin.text
