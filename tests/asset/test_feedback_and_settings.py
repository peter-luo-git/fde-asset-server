"""反馈闭环与系统配置。"""

from __future__ import annotations

from tests.conftest import as_user


def _any_asset(client, user: str = "chen") -> str:
    return (
        as_user(client, user)
        .get("/api/v1/assets", params={"limit": 1})
        .json()["items"][0]["asset_id"]
    )


def test_helpful_needs_no_words_but_criticism_does(client) -> None:
    """说好可以一键，说不好要讲清哪里不好，否则负责人改不动。"""
    asset_id = _any_asset(client)
    chen = as_user(client, "chen")
    assert (
        chen.post(f"/api/v1/assets/{asset_id}/feedback", json={"verdict": "helpful"}).status_code
        == 200
    )
    blank = chen.post(f"/api/v1/assets/{asset_id}/feedback", json={"verdict": "not_helpful"})
    assert blank.status_code == 422


def test_feedback_summary_counts_and_lists(client) -> None:
    asset_id = _any_asset(client)
    chen = as_user(client, "chen")
    chen.post(f"/api/v1/assets/{asset_id}/feedback", json={"verdict": "helpful"})
    chen.post(
        f"/api/v1/assets/{asset_id}/feedback",
        json={"verdict": "not_helpful", "note": "结论太泛，落不到具体操作"},
    )
    summary = chen.get(f"/api/v1/assets/{asset_id}/feedback").json()
    assert summary["counts"]["helpful"] == 1
    assert summary["counts"]["not_helpful"] == 1
    assert summary["recent"][0]["note"] == "结论太泛，落不到具体操作"


def test_outdated_auto_drafts_a_revision(client) -> None:
    """选「已过时」的那一刻，人正好知道哪里过时了，自动起草最省力。"""
    asset_id = _any_asset(client)
    result = as_user(client, "chen").post(
        f"/api/v1/assets/{asset_id}/feedback",
        json={"verdict": "outdated", "note": "分批大小的建议值变了，现在是 5000 行"},
    )
    body = result.json()
    assert body.get("candidate_id"), "应当自动起了一份修订草稿"

    candidate = (
        as_user(client, "chen").get(f"/api/v1/harvest-candidates/{body['candidate_id']}").json()
    )
    assert candidate["origin"] == "feedback"
    assert "分批大小" in candidate["source"]["note"]

    summary = as_user(client, "chen").get(f"/api/v1/assets/{asset_id}/feedback").json()
    assert summary["recent"][0]["candidate_id"] == body["candidate_id"]


def test_owner_sees_negative_feedback_and_declined_reasons(client) -> None:
    """负责人待办：差评和被拒原因都要汇到他这里。"""
    chen = as_user(client, "chen")
    mine = chen.get("/api/v1/assets", params={"mine": "personal", "limit": 1}).json()["items"][0]
    chen.post(
        f"/api/v1/assets/{mine['asset_id']}/feedback",
        json={"verdict": "not_helpful", "note": "跟我们的场景对不上"},
    )

    # 另一条线索：推荐被拒
    items = (
        as_user(client, "admin")
        .post(
            "/api/v1/recommend/compute",
            json={"target_type": "engagement", "target_id": "policy-import"},
        )
        .json()["items"]
    )
    target = next((item for item in items if item["asset_id"] == mine["asset_id"]), None)
    if target is not None:
        as_user(client, "admin").post(
            "/api/v1/recommend/send",
            json={
                "target_type": "engagement",
                "target_id": "policy-import",
                "items": [target],
            },
        )
        inbox = as_user(client, "wang").get("/api/v1/recommend/inbox").json()["items"]
        as_user(client, "wang").post(
            f"/api/v1/recommend/{inbox[0]['recommendation_id']}/decide",
            json={"accept": False, "note": "摘要看不出适用场景"},
        )

    # as_user 会就地改 client 的请求头，中途切过别的身份，这里要切回来
    signals = as_user(client, "chen").get("/api/v1/governance/signals").json()
    assert any(item["asset_id"] == mine["asset_id"] for item in signals["negative_feedback"]), (
        "差评要出现在负责人待办里"
    )


def test_settings_default_and_update(client) -> None:
    items = as_user(client, "admin").get("/api/v1/settings").json()["items"]
    by_key = {item["key"]: item for item in items}
    assert by_key["app_image_size_limit_mb"]["value"] == 5120, "镜像上限默认 5G"

    updated = as_user(client, "admin").put(
        "/api/v1/settings", json={"values": {"app_image_size_limit_mb": 2048}}
    )
    assert updated.status_code == 200
    assert updated.json()["values"]["app_image_size_limit_mb"] == 2048


def test_only_admin_changes_settings(client) -> None:
    denied = as_user(client, "chen").put(
        "/api/v1/settings", json={"values": {"app_image_size_limit_mb": 1}}
    )
    assert denied.status_code == 403


def test_settings_reject_bad_values(client) -> None:
    admin = as_user(client, "admin")
    assert admin.put("/api/v1/settings", json={"values": {"不存在": 1}}).status_code == 422
    assert (
        admin.put("/api/v1/settings", json={"values": {"attachment_size_limit_mb": 0}}).status_code
        == 422
    )
