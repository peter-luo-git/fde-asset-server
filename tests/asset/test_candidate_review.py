"""草稿的评审流程：作者要看得到该谁评、评了没有、被打回的理由是什么。"""

from __future__ import annotations

from tests.conftest import as_user


def _submit_ready_case(client) -> str:
    """造一份能通过校验的问题资产草稿并提交。"""
    chen = as_user(client, "chen")
    candidate = chen.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Case",
            "name": "review-flow-demo",
            "title": "评审流程演示",
            "scope": "engagement",
            "engagement_slug": "policy-import",
        },
    ).json()
    cid = candidate["candidate_id"]
    chen.patch(
        f"/api/v1/harvest-candidates/{cid}",
        json={
            "meta": {
                "summary": "批次过大导致导入超时，按 2000 行分批",
                "suitable": "批量导入任务",
                "notSuitable": "流式接口",
            },
            "files": {
                "README.md": (
                    "# 评审流程演示\n\n## 结论\n按 2000 行分批提交。\n\n"
                    "## 现象\n导入 12000 行报超时。\n\n## 根因\n单事务过大。\n\n"
                    "## 解决方法\n分批提交并重试当前批次。\n"
                )
            },
        },
    )
    # caseType 是问题类资产的必填 spec 字段，补进已经写好的 asset.yaml
    manifest = chen.get(f"/api/v1/harvest-candidates/{cid}").json()["files"]["asset.yaml"]
    chen.patch(
        f"/api/v1/harvest-candidates/{cid}",
        json={
            "files": {
                "asset.yaml": manifest.replace(
                    "spec:", "spec:\n  caseType: fault\n  severity: S2", 1
                )
            }
        },
    )
    submitted = chen.post(f"/api/v1/harvest-candidates/{cid}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    return cid


def test_draft_shows_who_can_review_before_submitting(client) -> None:
    chen = as_user(client, "chen")
    cid = chen.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Case",
            "name": "who-reviews",
            "title": "谁来评",
            "scope": "engagement",
            "engagement_slug": "policy-import",
        },
    ).json()["candidate_id"]

    review = as_user(client, "chen").get(f"/api/v1/harvest-candidates/{cid}/review").json()
    assert review["stage"] == "draft"
    assert review["reviewer_rule"] == "本项目负责人"
    # 老王是 policy-import 的 owner，应当出现在评审人名单里；小陈自己不评自己
    assert "wang" in [item["user_id"] for item in review["reviewers"]]
    assert "chen" not in [item["user_id"] for item in review["reviewers"]]
    assert review["history"] == []


def test_waiting_stage_after_submit(client) -> None:
    cid = _submit_ready_case(client)
    review = as_user(client, "chen").get(f"/api/v1/harvest-candidates/{cid}/review").json()
    assert review["stage"] == "waiting"
    assert review["current"] is not None
    assert review["current"]["submitted_by"] == "chen"
    assert review["can_review_myself"] is False


def test_author_sees_the_rejection_note_and_can_edit_again(client) -> None:
    """被打回要能看到意见，而且候选回到草稿状态，作者才改得动。"""
    cid = _submit_ready_case(client)
    review_id = (
        as_user(client, "chen")
        .get(f"/api/v1/harvest-candidates/{cid}/review")
        .json()["current"]["review_id"]
    )

    decided = as_user(client, "wang").post(
        f"/api/v1/reviews/{review_id}/decide",
        json={"approve": False, "note": "根因写得太浅，补一下为什么事务会超时"},
    )
    assert decided.status_code == 200, decided.text

    review = as_user(client, "chen").get(f"/api/v1/harvest-candidates/{cid}/review").json()
    assert review["stage"] == "rejected"
    assert review["history"][0]["note"] == "根因写得太浅，补一下为什么事务会超时"
    assert review["history"][0]["decided_by"] == "wang"

    candidate = as_user(client, "chen").get(f"/api/v1/harvest-candidates/{cid}").json()
    assert candidate["status"] == "draft", "打回后要回到草稿，否则作者改不了"
    patched = as_user(client, "chen").patch(
        f"/api/v1/harvest-candidates/{cid}", json={"meta": {"summary": "改过的结论"}}
    )
    assert patched.status_code == 200


def test_history_keeps_every_round(client) -> None:
    cid = _submit_ready_case(client)
    first = (
        as_user(client, "chen").get(f"/api/v1/harvest-candidates/{cid}/review").json()["current"]
    )
    as_user(client, "wang").post(
        f"/api/v1/reviews/{first['review_id']}/decide",
        json={"approve": False, "note": "第一轮意见"},
    )
    as_user(client, "chen").post(f"/api/v1/harvest-candidates/{cid}/submit", json={})

    review = as_user(client, "chen").get(f"/api/v1/harvest-candidates/{cid}/review").json()
    assert len(review["history"]) == 2, "两轮评审都要留痕"
    assert review["stage"] == "waiting"
    assert [item["status"] for item in review["history"]] == ["open", "rejected"]


def test_reviewer_sees_can_review_myself(client) -> None:
    cid = _submit_ready_case(client)
    review = as_user(client, "wang").get(f"/api/v1/harvest-candidates/{cid}/review").json()
    assert review["can_review_myself"] is True


def test_unrelated_user_still_cannot_open_the_draft(client) -> None:
    """放开给评审人看，不等于谁都能看：小赵既不是作者也无评审权。"""
    cid = _submit_ready_case(client)
    assert as_user(client, "zhao").get(f"/api/v1/harvest-candidates/{cid}").status_code == 403


def test_reviewer_cannot_peek_before_submission(client) -> None:
    """没提交之前草稿是私有的，项目负责人也看不到。"""
    chen = as_user(client, "chen")
    cid = chen.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Case",
            "name": "not-yet-submitted",
            "title": "还没提交",
            "scope": "engagement",
            "engagement_slug": "policy-import",
        },
    ).json()["candidate_id"]
    assert as_user(client, "wang").get(f"/api/v1/harvest-candidates/{cid}").status_code == 403


def test_review_queue_knows_who_can_decide(client) -> None:
    """「待我评审」要按真实归属算权限：项目级评审归项目负责人。"""
    cid = _submit_ready_case(client)

    wang = as_user(client, "wang").get("/api/v1/reviews", params={"mine": True}).json()["items"]
    assert any(item["candidate_id"] == cid for item in wang), "老王是项目负责人，该看到"
    assert all(item["can_decide"] for item in wang)
    assert wang[0]["title"] == "评审流程演示", "队列里要能看出是什么资产"

    zhao = as_user(client, "zhao").get("/api/v1/reviews", params={"mine": True}).json()["items"]
    assert zhao == [], "不相干的人队列是空的"
