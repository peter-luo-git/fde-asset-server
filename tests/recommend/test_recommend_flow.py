"""推荐全链路：算 → 推 → 收件箱 → 接收或忽略 → 关联生效。"""

from __future__ import annotations

from tests.conftest import as_user


def _compute(client, user: str, target_type: str, target_id: str) -> list[dict]:
    response = as_user(client, user).post(
        "/api/v1/recommend/compute",
        json={"target_type": target_type, "target_id": target_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_targets_split_mine_and_department(client) -> None:
    """我负责的和我部门的分开列；普通成员看不到部门视图。"""
    wang = as_user(client, "wang").get("/api/v1/recommend/targets").json()
    assert {item["target_id"] for item in wang["mine"]} >= {"policy-import", "core-migration"}
    # 老王兼 finance 部门主管：部门视图里是本部门的全部项目和 Agent，
    # 并分得清哪些是他自己负责的（直接关联）、哪些是同事负责的（推送）
    by_id = {item["target_id"]: item for item in wang["department"]}
    assert set(by_id) == {"policy-import", "core-migration", "migration-reviewer", "claims-recon"}
    assert {key for key, item in by_id.items() if item["mine"]} == {
        "policy-import",
        "core-migration",
        "migration-reviewer",
    }
    assert by_id["claims-recon"]["mine"] is False
    assert by_id["claims-recon"]["owner"] == "sun"
    assert by_id["claims-recon"]["owner_name"] == "小孙"
    assert all(item["mine"] for item in wang["mine"])

    # 小孙自己看：这个项目在「我负责的」里；他不是主管，没有部门视图
    sun = as_user(client, "sun").get("/api/v1/recommend/targets").json()
    assert [item["target_id"] for item in sun["mine"]] == ["claims-recon"]
    assert sun["department"] == []

    chen = as_user(client, "chen").get("/api/v1/recommend/targets").json()
    assert chen["mine"], "小陈有自己负责的项目"
    assert chen["department"] == [], "小陈不是部门主管，没有部门视图"

    admin = as_user(client, "admin").get("/api/v1/recommend/targets").json()
    assert len(admin["mine"]) >= 5


def test_compute_uses_target_context_not_just_the_id(client) -> None:
    """匹配读的是项目的行业与描述，不是靠标识猜。"""
    items = _compute(client, "wang", "engagement", "policy-import")
    assert items, "保险行业的导入项目应该能匹配到资产"
    assert all(item["reasons"] for item in items), "每条推荐都要有理由"
    # 规范一律带上
    assert any("规范强制注入" in item["reasons"] for item in items)


def test_department_head_sends_and_owner_decides(client) -> None:
    """部门主管只能推荐，收不收由负责人定。"""
    items = _compute(client, "admin", "engagement", "policy-import")[:3]
    sent = as_user(client, "admin").post(
        "/api/v1/recommend/send",
        json={"target_type": "engagement", "target_id": "policy-import", "items": items},
    )
    assert sent.status_code == 200, sent.text
    body = sent.json()
    # 管理员看得到的资产，老王不一定看得到；推之前会先过滤掉，不制造死待办
    assert body["created"] + len(body["skipped_invisible"]) == 3

    # 老王是 policy-import 的负责人，应该在收件箱里看到剩下的
    inbox = as_user(client, "wang").get("/api/v1/recommend/inbox").json()["items"]
    assert len(inbox) == body["created"]
    assert all(item["status"] == "sent" for item in inbox)

    # 不相干的人收件箱是空的
    assert as_user(client, "zhao").get("/api/v1/recommend/inbox").json()["items"] == []


def test_accept_writes_the_link(client) -> None:
    items = _compute(client, "admin", "engagement", "policy-import")[:1]
    as_user(client, "admin").post(
        "/api/v1/recommend/send",
        json={"target_type": "engagement", "target_id": "policy-import", "items": items},
    )
    recommendation = as_user(client, "wang").get("/api/v1/recommend/inbox").json()["items"][0]

    decided = as_user(client, "wang").post(
        f"/api/v1/recommend/{recommendation['recommendation_id']}/decide", json={"accept": True}
    )
    assert decided.json()["status"] == "accepted"

    linked = (
        as_user(client, "wang")
        .get(
            "/api/v1/recommend/linked",
            params={"target_type": "engagement", "target_id": "policy-import"},
        )
        .json()["items"]
    )
    assert [item["asset_id"] for item in linked] == [recommendation["asset_id"]]
    # 收件箱清空
    assert as_user(client, "wang").get("/api/v1/recommend/inbox").json()["items"] == []


def test_decline_requires_a_reason(client) -> None:
    """忽略必须写原因——反复被拒是资产该改的最直接信号。"""
    items = _compute(client, "admin", "engagement", "policy-import")[:1]
    as_user(client, "admin").post(
        "/api/v1/recommend/send",
        json={"target_type": "engagement", "target_id": "policy-import", "items": items},
    )
    recommendation = as_user(client, "wang").get("/api/v1/recommend/inbox").json()["items"][0]

    blank = as_user(client, "wang").post(
        f"/api/v1/recommend/{recommendation['recommendation_id']}/decide", json={"accept": False}
    )
    assert blank.status_code == 422

    ok = as_user(client, "wang").post(
        f"/api/v1/recommend/{recommendation['recommendation_id']}/decide",
        json={"accept": False, "note": "我们这个项目不走批量导入"},
    )
    assert ok.json()["status"] == "declined"


def test_resend_updates_instead_of_duplicating(client) -> None:
    items = _compute(client, "admin", "engagement", "policy-import")[:2]
    payload = {"target_type": "engagement", "target_id": "policy-import", "items": items}
    first = as_user(client, "admin").post("/api/v1/recommend/send", json=payload).json()
    second = as_user(client, "admin").post("/api/v1/recommend/send", json=payload).json()
    assert first["created"] == 2
    assert second["created"] == 0 and second["updated"] == 2

    inbox = as_user(client, "wang").get("/api/v1/recommend/inbox").json()["items"]
    assert len(inbox) == 2, "重复推送不该刷屏"


def test_outsider_cannot_send(client) -> None:
    items = _compute(client, "admin", "engagement", "policy-import")[:1]
    denied = as_user(client, "zhao").post(
        "/api/v1/recommend/send",
        json={"target_type": "engagement", "target_id": "policy-import", "items": items},
    )
    assert denied.status_code == 403


def test_owner_links_directly_without_recommendation(client) -> None:
    """本人关联不走推荐流程，一步到位。"""
    items = _compute(client, "wang", "engagement", "policy-import")[:1]
    linked = as_user(client, "wang").post(
        "/api/v1/recommend/link",
        json={
            "target_type": "engagement",
            "target_id": "policy-import",
            "asset_id": items[0]["asset_id"],
        },
    )
    assert linked.json()["linked"] is True
    again = as_user(client, "wang").post(
        "/api/v1/recommend/link",
        json={
            "target_type": "engagement",
            "target_id": "policy-import",
            "asset_id": items[0]["asset_id"],
        },
    )
    assert again.json()["linked"] is False


def test_linked_assets_are_not_recommended_again(client) -> None:
    items = _compute(client, "wang", "engagement", "policy-import")
    first = items[0]["asset_id"]
    as_user(client, "wang").post(
        "/api/v1/recommend/link",
        json={"target_type": "engagement", "target_id": "policy-import", "asset_id": first},
    )
    again = _compute(client, "wang", "engagement", "policy-import")
    assert first not in [item["asset_id"] for item in again]


def test_agent_target_uses_its_own_context(client) -> None:
    """Agent 的匹配读预设的角色与描述，标识只是收件地址。"""
    items = _compute(client, "chen", "agent", "import-coder")
    assert items
    assert any("关键词命中" in reason for item in items for reason in item["reasons"]), (
        "Agent 的描述里有导入、超时等词，应当命中"
    )


def test_department_head_pushes_to_a_colleague_but_links_own_directly(client) -> None:
    """部门主管：同事负责的项目只能推送，由对方确认；自己负责的直接关联。"""
    wang = as_user(client, "wang")
    target = {"target_type": "engagement", "target_id": "claims-recon"}
    items = wang.post("/api/v1/recommend/compute", json=target).json()["items"][:2]
    assert items

    # 替同事直接关联：不行
    refused = wang.post("/api/v1/recommend/link", json={**target, "asset_id": items[0]["asset_id"]})
    assert refused.status_code == 403

    sent = wang.post("/api/v1/recommend/send", json={**target, "items": items})
    assert sent.status_code == 200, sent.text
    assert sent.json()["created"] + len(sent.json()["skipped_invisible"]) == len(items)

    inbox = as_user(client, "sun").get("/api/v1/recommend/inbox").json()["items"]
    assert len(inbox) == sent.json()["created"]
    assert all(item["status"] == "sent" for item in inbox)
    assert as_user(client, "wang").get("/api/v1/recommend/inbox").json()["items"] == []

    # 自己负责的：直接关联，不产生待确认
    own = {"target_type": "engagement", "target_id": "policy-import"}
    mine = as_user(client, "wang").post("/api/v1/recommend/compute", json=own).json()["items"][0]
    linked = as_user(client, "wang").post(
        "/api/v1/recommend/link", json={**own, "asset_id": mine["asset_id"]}
    )
    assert linked.status_code == 200, linked.text
