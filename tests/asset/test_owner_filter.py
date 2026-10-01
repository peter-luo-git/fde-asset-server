"""按负责人筛选：个人负责的和部门负责的要能分开看。"""

from __future__ import annotations

from tests.conftest import as_user


def _titles(payload: dict) -> list[str]:
    return [item["title"] for item in payload["items"]]


def test_owner_kind_splits_person_and_department(client) -> None:
    chen = as_user(client, "chen")
    personal = chen.get("/api/v1/assets", params={"owner_kind": "user", "limit": 100}).json()
    department = chen.get(
        "/api/v1/assets", params={"owner_kind": "department", "limit": 100}
    ).json()
    everything = chen.get("/api/v1/assets", params={"limit": 100}).json()

    assert personal["total"] + department["total"] == everything["total"]
    assert all(item["owner_ref"].startswith("user:") for item in personal["items"])
    assert all(item["owner_ref"].startswith("department:") for item in department["items"])


def test_mine_personal_only_returns_my_own(client) -> None:
    chen = as_user(client, "chen")
    mine = chen.get("/api/v1/assets", params={"mine": "personal", "limit": 100}).json()
    assert mine["total"] > 0
    assert all(item["owner_ref"] == "user:chen" for item in mine["items"])


def test_mine_department_returns_my_department(client) -> None:
    chen = as_user(client, "chen")
    mine = chen.get("/api/v1/assets", params={"mine": "department", "limit": 100}).json()
    assert mine["total"] > 0
    assert all(item["owner_ref"] == "department:data-intel" for item in mine["items"])


def test_mine_any_equals_personal_plus_department(client) -> None:
    """「我负责的」口径要和工作台一致：我本人 + 我所在部门。"""
    chen = as_user(client, "chen")
    personal = chen.get("/api/v1/assets", params={"mine": "personal", "limit": 100}).json()
    department = chen.get("/api/v1/assets", params={"mine": "department", "limit": 100}).json()
    both = chen.get("/api/v1/assets", params={"mine": "any", "limit": 100}).json()

    assert both["total"] == personal["total"] + department["total"]
    workbench = chen.get("/api/v1/workbench").json()
    assert len(workbench["owned"]) == both["total"]


def test_workbench_owned_tells_person_from_department(client) -> None:
    owned = as_user(client, "chen").get("/api/v1/workbench").json()["owned"]
    kinds = {item["owner_kind"] for item in owned}
    assert kinds == {"user", "department"}, f"应当两种都有，实际 {kinds}"


def test_owner_filter_still_respects_visibility(client) -> None:
    """按负责人筛选不能绕过可见性：小赵看不到小陈项目里的资产。"""
    zhao = (
        as_user(client, "zhao")
        .get("/api/v1/assets", params={"owner": "user:chen", "limit": 100})
        .json()
    )
    assert all(item["scope"] == "company" for item in zhao["items"])
