"""客户级草稿：要带客户代号，进对应的客户仓库，由评审员或该客户的项目负责人评。"""

from __future__ import annotations

from tests.conftest import as_user

BODY = (
    "# 华安对账口径约定\n\n## 结论\n以保单号加批次为键。\n\n## 客户类型与场景\n保险。\n\n"
    "## 方案概述\n逐批对账。\n\n## 适用边界\n只适用于华安。\n"
)


def _create(client, **extra):
    body = {"kind": "Solution", "title": "华安对账口径约定", "scope": "customer"}
    body.update(extra)
    return client.post("/api/v1/harvest-candidates", json=body)


def test_customer_draft_needs_a_customer_the_author_serves(client) -> None:
    chen = as_user(client, "chen")
    missing = _create(chen)
    assert missing.status_code == 422 and "customer_code" in missing.json()["detail"]

    # 小赵没有参与华安的项目；否则任何人都能往别的客户仓库里塞东西
    outsider = _create(as_user(client, "zhao"), customer_code="HUAAN")
    assert outsider.status_code == 422 and "没有参与客户 HUAAN" in outsider.json()["detail"]

    other = _create(as_user(client, "chen"), customer_code="OTHER")
    assert other.status_code == 422

    ok = _create(as_user(client, "chen"), customer_code="HUAAN")
    assert ok.status_code == 200, ok.text
    assert ok.json()["scope"] == "customer" and ok.json()["customer_code"] == "HUAAN"

    # 不是客户作用域就不记客户代号，免得留下一个对不上的值
    plain = chen.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Case",
            "title": "部门级的",
            "scope": "department",
            "department_code": "data-intel",
            "customer_code": "HUAAN",
        },
    )
    assert plain.json()["customer_code"] == ""


def test_customer_draft_lands_in_the_customer_repo_after_review(client) -> None:
    chen = as_user(client, "chen")
    candidate_id = _create(chen, customer_code="HUAAN").json()["candidate_id"]
    chen.patch(
        f"/api/v1/harvest-candidates/{candidate_id}",
        json={
            "files": {"README.md": BODY},
            "meta": {
                "summary": "对账以保单号加批次为键",
                "suitable": "华安的各个项目",
                "notSuitable": "其它客户",
            },
        },
    )
    submitted = chen.post(f"/api/v1/harvest-candidates/{candidate_id}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    review_id = submitted.json()["review_id"]

    def may_decide(user: str) -> bool:
        queue = as_user(client, user).get(
            "/api/v1/reviews", params={"status": "open", "mine": False}
        )
        mine = [item for item in queue.json()["items"] if item["review_id"] == review_id]
        return bool(mine and mine[0]["can_decide"])

    assert may_decide("wang"), "该客户项目的负责人可以评"
    assert may_decide("li"), "资产评审员可以评"
    assert not may_decide("chen"), "普通成员不行，何况是自己提交的"

    # 评审通知发给能评的人
    inbox = as_user(client, "wang").get("/api/v1/notifications", params={"unread_only": False})
    assert any(
        item["kind"] == "review" and item["asset_id"] == candidate_id
        for item in inbox.json()["items"]
    )

    refused = as_user(client, "zhao").post(
        f"/api/v1/reviews/{review_id}/decide", json={"approve": True}
    )
    assert refused.status_code == 403

    done = as_user(client, "wang").post(
        f"/api/v1/reviews/{review_id}/decide", json={"approve": True}
    )
    assert done.status_code == 200, done.text

    visible = as_user(client, "chen").get(
        "/api/v1/assets", params={"scope": "customer", "limit": 100}
    )
    landed = [item for item in visible.json()["items"] if item["title"] == "华安对账口径约定"]
    assert landed, "入库后在客户级资产里"
    assert landed[0]["repo"] == "cust-HUAAN-assets" and landed[0]["customer_code"] == "HUAAN"

    hidden = as_user(client, "zhao").get("/api/v1/assets", params={"limit": 200}).json()["items"]
    assert all(item["title"] != "华安对账口径约定" for item in hidden)


def test_scope_can_be_set_to_customer_at_submit_time_only_for_members(client) -> None:
    zhao = as_user(client, "zhao")
    draft = zhao.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Case",
            "title": "想混进客户仓库",
            "scope": "department",
            "department_code": "market",
        },
    ).json()
    response = zhao.post(
        f"/api/v1/harvest-candidates/{draft['candidate_id']}/submit",
        json={"scope": "customer", "customer_code": "HUAAN"},
    )
    assert response.status_code == 403
    missing = zhao.post(
        f"/api/v1/harvest-candidates/{draft['candidate_id']}/submit", json={"scope": "customer"}
    )
    assert missing.status_code == 422
