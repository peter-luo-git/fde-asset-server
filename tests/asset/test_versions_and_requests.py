"""资产版本（网页上修改已入库资产）、可见范围申请审批、起草时多选可见范围。2026-10-17。"""

from __future__ import annotations

from sqlalchemy import select

from fde_asset.core.db import assets
from tests.conftest import as_user

CASE = "import-over-10k-rows-timeout"  # 项目级问题案例，负责人 chen，项目负责人 wang


def _row(client, name=CASE):
    with client.app.state.context.engine.connect() as conn:
        return conn.execute(select(assets).where(assets.c.name == name)).first()


def _review_id(client, candidate_id):
    return as_user(client, "chen").get(f"/api/v1/harvest-candidates/{candidate_id}/review").json()[
        "current"
    ]["review_id"]


# ---------- 可见范围申请 ----------


def test_adding_a_layer_you_cannot_review_files_a_request_instead_of_failing(client):
    row = _row(client)
    response = as_user(client, "chen").post(
        f"/api/v1/assets/{row.asset_id}/visibility",
        json={"visibility": ["engagement:policy-import", "user:sun", "department:data-intel"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    # 点名个人直接生效；部门这一层 chen 没有评审权，转成申请
    assert result["changed"] is True
    assert "user:sun" in result["visibility"]
    assert "department:data-intel" not in result["visibility"]
    assert [item["entry"] for item in result["requested"]] == ["department:data-intel"]
    request_id = result["requested"][0]["request_id"]

    # 再提一次不重复建
    again = as_user(client, "chen").post(
        f"/api/v1/assets/{row.asset_id}/visibility",
        json={"visibility": [*result["visibility"], "department:data-intel"]},
    ).json()
    assert again["requested"][0]["request_id"] == request_id

    # 资产详情页能看到在等的申请；申请人能撤回、不能自己批
    pending = as_user(client, "chen").get(f"/api/v1/assets/{row.asset_id}/visibility-requests").json()["items"]
    assert pending[0]["can_withdraw"] is True and pending[0]["can_decide"] is False

    # 别的部门的主管批不了；数据智能部的资产评审员（li）能批
    assert (
        as_user(client, "wang")
        .post(f"/api/v1/visibility-requests/{request_id}/decide", json={"approve": True})
        .status_code
        == 403
    )
    todo = as_user(client, "li").get("/api/v1/visibility-requests", params={"view": "todo"}).json()["items"]
    assert request_id in {item["request_id"] for item in todo}
    decided = as_user(client, "li").post(f"/api/v1/visibility-requests/{request_id}/decide", json={"approve": True})
    assert decided.status_code == 200, decided.text
    assert "department:data-intel" in _visibility(client)
    # 批准后申请人收到通知
    notes = as_user(client, "chen").get("/api/v1/notifications", params={"limit": 50}).json()
    assert any("可见范围申请已批准" in item["title"] for item in notes["items"])
    assert as_user(client, "chen").get(f"/api/v1/assets/{row.asset_id}/visibility-requests").json()[
        "items"
    ] == []


def _visibility(client):
    import json

    return json.loads(_row(client).visibility_json)


def test_rejecting_needs_a_reason_and_withdrawing_closes_the_request(client):
    row = _row(client)
    first = as_user(client, "chen").post(
        f"/api/v1/assets/{row.asset_id}/visibility",
        json={"visibility": ["engagement:policy-import", "department:data-intel"]},
    ).json()["requested"][0]
    url = f"/api/v1/visibility-requests/{first['request_id']}/decide"
    assert as_user(client, "li").post(url, json={"approve": False}).status_code == 422
    assert as_user(client, "li").post(url, json={"approve": False, "note": "部门内还没评估"}).json()["status"] == "rejected"
    assert "department:data-intel" not in _visibility(client)

    second = as_user(client, "chen").post(
        f"/api/v1/assets/{row.asset_id}/visibility",
        json={"visibility": ["engagement:policy-import", "department:data-intel"]},
    ).json()["requested"][0]
    assert second["request_id"] != first["request_id"]
    withdraw = f"/api/v1/visibility-requests/{second['request_id']}/withdraw"
    assert as_user(client, "li").post(withdraw).status_code == 403
    assert as_user(client, "chen").post(withdraw).json()["status"] == "withdrawn"
    assert as_user(client, "li").get("/api/v1/visibility-requests").json()["items"] == []


# ---------- 修改已入库资产，发布新版本 ----------


def test_editing_a_landed_asset_publishes_a_new_version_of_the_same_asset(client):
    row = _row(client)
    draft = as_user(client, "chen").post(f"/api/v1/assets/{row.asset_id}/edit")
    assert draft.status_code == 200, draft.text
    candidate = draft.json()
    assert candidate["revises_asset_id"] == row.asset_id
    assert candidate["name"] == CASE and "README.md" in candidate["files"]
    # 再点一次「修改」回到同一份修订稿
    assert as_user(client, "chen").post(f"/api/v1/assets/{row.asset_id}/edit").json()["candidate_id"] == candidate[
        "candidate_id"
    ]
    # 修订稿没入库前，别人不能再起一份
    assert as_user(client, "wang").post(f"/api/v1/assets/{row.asset_id}/edit").status_code == 409
    detail = as_user(client, "chen").get(f"/api/v1/assets/{row.asset_id}").json()
    assert detail["open_revision"]["candidate_id"] == candidate["candidate_id"]

    body = candidate["files"]["README.md"] + "\n\n## 补充\n2026-10 起批次改为 1500 行。\n"
    patched = as_user(client, "chen").patch(
        f"/api/v1/harvest-candidates/{candidate['candidate_id']}", json={"files": {"README.md": body}}
    )
    assert patched.status_code == 200, patched.text
    submitted = as_user(client, "chen").post(f"/api/v1/harvest-candidates/{candidate['candidate_id']}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    review_id = _review_id(client, candidate["candidate_id"])
    decided = as_user(client, "wang").post(
        f"/api/v1/reviews/{review_id}/decide",
        json={"approve": True},
    )
    assert decided.status_code == 200, decided.text

    after = _row(client)
    assert after.asset_id == row.asset_id and after.path == row.path
    assert after.version != row.version
    assert "1500 行" in after.content_text
    # 没有因为修订多出一份同名资产
    with client.app.state.context.engine.connect() as conn:
        same_name = conn.execute(
            select(assets.c.asset_id).where(assets.c.name == CASE, assets.c.deleted_at.is_(None))
        ).fetchall()
    assert len(same_name) == 1
    versions = as_user(client, "chen").get(f"/api/v1/assets/{row.asset_id}/versions").json()["items"]
    assert versions[0]["version"] == after.version
    assert as_user(client, "chen").get(f"/api/v1/assets/{row.asset_id}").json()["open_revision"] is None


def test_only_managers_can_edit_and_versions_bump_sensibly(client):
    from fde_asset.modules.harvest.service import bump_version

    row = _row(client)
    assert as_user(client, "zhao").post(f"/api/v1/assets/{row.asset_id}/edit").status_code == 404
    assert as_user(client, "sun").post(f"/api/v1/assets/{row.asset_id}/edit").status_code in (403, 404)
    assert bump_version("1.2.3", "1.2.3") == "1.3.0"
    assert bump_version("1.2.3", "0.1.0") == "1.3.0"
    assert bump_version("1.2.3", "2.0.0") == "2.0.0"
    assert bump_version("weird", "0.2.0") == "0.2.0"


def test_outdated_feedback_drafts_a_revision_of_the_same_asset(client):
    row = _row(client, "policy-date-parse")
    response = as_user(client, "wang").post(
        f"/api/v1/assets/{row.asset_id}/feedback",
        json={"verdict": "outdated", "note": "日期格式新增了 yyyy/MM/dd"},
    )
    assert response.status_code == 200, response.text
    candidate_id = response.json()["candidate_id"]
    candidate = as_user(client, "wang").get(f"/api/v1/harvest-candidates/{candidate_id}").json()
    assert candidate["revises_asset_id"] == row.asset_id
    assert candidate["name"] == "policy-date-parse"
    # 负责人 chen 能打开这份修订稿
    assert as_user(client, "chen").get(f"/api/v1/harvest-candidates/{candidate_id}").status_code == 200


# ---------- 起草时多选可见范围 ----------


def test_extra_visibility_chosen_while_drafting_lands_or_becomes_requests(client):
    from tests.asset.test_candidate_review import _submit_ready_case

    cid = _submit_ready_case(client)
    # 已提交的草稿不能再改；这里先打回再改可见范围
    review_id = _review_id(client, cid)
    as_user(client, "wang").post(
        f"/api/v1/reviews/{review_id}/decide", json={"approve": False, "note": "补可见范围"}
    )
    bad = as_user(client, "chen").patch(f"/api/v1/harvest-candidates/{cid}", json={"visibility": ["team:x"]})
    assert bad.status_code == 409
    ok = as_user(client, "chen").patch(
        f"/api/v1/harvest-candidates/{cid}",
        json={"visibility": ["user:sun", "department:data-intel"]},
    )
    assert ok.status_code == 200 and ok.json()["visibility"] == ["user:sun", "department:data-intel"]
    assert as_user(client, "chen").post(f"/api/v1/harvest-candidates/{cid}/submit", json={}).status_code == 200
    review_id = _review_id(client, cid)
    result = as_user(client, "wang").post(
        f"/api/v1/reviews/{review_id}/decide", json={"approve": True}
    ).json()
    # 项目负责人 wang 能点名个人，部门这一层要数据智能部的评审人批
    assert result["visibility"]["applied"] == ["user:sun"]
    assert result["visibility"]["requested"] == ["department:data-intel"]
    asset_id = result.get("asset_id") or _row(client, "review-flow-demo").asset_id
    pending = as_user(client, "li").get("/api/v1/visibility-requests").json()["items"]
    assert [(item["asset_id"], item["requested_by"]) for item in pending] == [(asset_id, "chen")]


def test_audience_options_list_departments_customers_projects_and_people(client):
    options = as_user(client, "chen").get("/api/v1/audience-options").json()
    assert {"data-intel", "finance"} <= {item["code"] for item in options["departments"]}
    assert "HUAAN" in {item["code"] for item in options["customers"]}
    assert "policy-import" in {item["code"] for item in options["engagements"]}
    assert "chen" in {item["code"] for item in options["users"]}


def test_detail_body_returns_the_full_main_file_for_viewers_only(client):
    row = _row(client)
    body = as_user(client, "chen").get(f"/api/v1/assets/{row.asset_id}/body")
    assert body.status_code == 200, body.text
    data = body.json()
    assert data["file"] == "README.md"
    assert "## 根因" in data["markdown"] or "## 解决方法" in data["markdown"]
    assert as_user(client, "zhao").get(f"/api/v1/assets/{row.asset_id}/body").status_code == 404
