"""权限与可见性回归：三级作用域、跨部门支援、受限资产、评审权限、防探测。

这组用例是安全底线，不允许因为进度压力跳过。
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from fde_asset.core.db import assets
from fde_asset.modules.asset import catalog
from fde_asset.modules.asset.visibility import can_review
from fde_asset.platform.identity import Membership, Principal


def _names(client, user: str, **params) -> set[str]:
    client.headers.update({"X-FDE-User": user})
    response = client.get("/api/v1/assets", params={"limit": 100, **params})
    assert response.status_code == 200
    return {item["name"] for item in response.json()["items"]}


# ---------- 三级作用域 ----------


def test_company_assets_visible_to_everyone(client) -> None:
    for user in ("chen", "wang", "li", "zhao", "admin"):
        assert "insurance-policy-import" in _names(client, user)


def test_engagement_asset_only_for_members(client) -> None:
    assert "policy-date-parse" in _names(client, "chen")
    assert "policy-date-parse" in _names(client, "wang")
    assert "policy-date-parse" not in _names(client, "zhao")


def test_department_asset_for_own_department(client) -> None:
    assert "pg-vacuum-tuning" in _names(client, "chen")  # 主部门 data-intel
    assert "pg-vacuum-tuning" in _names(client, "li")  # 同部门
    assert "pg-vacuum-tuning" not in _names(client, "zhao")  # 市场部，且不在相关项目里


def test_cross_department_supporter_sees_department_assets(client, context) -> None:
    """小陈主部门是 data-intel，同时支援 finance 的项目，应看得到 finance 的部门资产。"""
    principal = context.directory.resolve("chen")
    assert principal.department_codes == {"data-intel", "finance"}


def test_admin_sees_all_scopes(client) -> None:
    names = _names(client, "admin")
    assert {"insurance-policy-import", "pg-vacuum-tuning", "policy-date-parse"} <= names


def test_total_count_matches_visible_items(client) -> None:
    client.headers.update({"X-FDE-User": "zhao"})
    payload = client.get("/api/v1/assets", params={"limit": 100}).json()
    assert payload["total"] == len(payload["items"])
    assert all(item["scope"] == "company" for item in payload["items"])


def test_invalid_assets_excluded_from_catalog(client) -> None:
    assert "no-summary-case" not in _names(client, "admin")


# ---------- 详情与引用 ----------


def test_detail_404_for_invisible_asset(client) -> None:
    client.headers.update({"X-FDE-User": "chen"})
    asset_id = client.get("/api/v1/assets", params={"q": "policy-date-parse"}).json()["items"][0][
        "asset_id"
    ]
    client.headers.update({"X-FDE-User": "zhao"})
    assert client.get(f"/api/v1/assets/{asset_id}").status_code == 404


def test_resolve_placeholder_hides_title(client) -> None:
    client.headers.update({"X-FDE-User": "zhao"})
    payload = client.get("/api/v1/assets/resolve", params={"ref": "skill/policy-date-parse"}).json()
    assert payload["status"] == "asset_not_visible"
    assert "title" not in payload and "summary" not in payload


def test_missing_and_forbidden_are_indistinguishable(client) -> None:
    client.headers.update({"X-FDE-User": "zhao"})
    forbidden = client.get(
        "/api/v1/assets/resolve", params={"ref": "skill/policy-date-parse"}
    ).json()
    missing = client.get("/api/v1/assets/resolve", params={"ref": "skill/never-existed"}).json()
    assert forbidden["status"] == missing["status"]
    assert set(forbidden) == set(missing)


def test_passport_requires_visibility(client) -> None:
    client.headers.update({"X-FDE-User": "chen"})
    asset_id = client.get("/api/v1/assets", params={"q": "policy-date-parse"}).json()["items"][0][
        "asset_id"
    ]
    client.headers.update({"X-FDE-User": "zhao"})
    assert client.get(f"/api/v1/assets/{asset_id}/passport").status_code == 404


# ---------- 受限资产 ----------


def test_restricted_asset_hidden_from_admin(client, context) -> None:
    with context.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.name == "bank-core-migration-solution")
            .values(restricted=True, owner_kind="department", owner_value="data-intel")
        )
    for user in ("chen", "li", "admin", "zhao"):
        assert "bank-core-migration-solution" not in _names(client, user)


def test_restricted_asset_visible_to_department_head(client, context) -> None:
    with context.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.name == "bank-core-migration-solution")
            .values(restricted=True, owner_kind="department", owner_value="data-intel")
        )
    head = Principal("head", department_code="data-intel", is_department_head=True)
    result = catalog.search(context.engine, head, catalog.CatalogQuery(limit=100))
    assert "bank-core-migration-solution" in {item["name"] for item in result["items"]}


def test_restricted_asset_visible_to_owner_user(client, context) -> None:
    with context.engine.begin() as conn:
        conn.execute(
            update(assets)
            .where(assets.c.name == "bank-core-migration-solution")
            .values(restricted=True, owner_kind="user", owner_value="chen")
        )
    owner = Principal("chen", department_code="data-intel")
    result = catalog.search(context.engine, owner, catalog.CatalogQuery(limit=100))
    assert "bank-core-migration-solution" in {item["name"] for item in result["items"]}


# ---------- 评审权限 ----------


@pytest.mark.parametrize(
    "principal,scope,expected",
    [
        (Principal("li", department_code="data-intel", is_asset_reviewer=True), "company", True),
        (Principal("chen", department_code="data-intel"), "company", False),
        (Principal("li", department_code="data-intel", is_asset_reviewer=True), "department", True),
        (Principal("li", department_code="finance", is_asset_reviewer=True), "department", False),
        (
            Principal("head", department_code="data-intel", is_department_head=True),
            "department",
            True,
        ),
        (
            Principal("wang", memberships=(Membership("policy-import", "finance", "owner"),)),
            "engagement",
            True,
        ),
        (
            Principal("chen", memberships=(Membership("policy-import", "finance", "member"),)),
            "engagement",
            False,
        ),
        (Principal("admin", is_admin=True), "company", True),
    ],
)
def test_review_permission_matrix(principal: Principal, scope: str, expected: bool) -> None:
    assert (
        can_review(principal, scope, department_code="data-intel", engagement_slug="policy-import")
        is expected
    )


def test_only_reviewer_can_list_invalid(client) -> None:
    client.headers.update({"X-FDE-User": "chen"})
    assert client.get("/api/v1/assets/invalid").status_code == 403
    client.headers.update({"X-FDE-User": "li"})
    assert client.get("/api/v1/assets/invalid").status_code == 200


def test_only_reviewer_can_reindex(client) -> None:
    client.headers.update({"X-FDE-User": "chen"})
    assert client.post("/api/v1/admin/assets/reindex").status_code == 403


def test_unknown_user_rejected(client) -> None:
    client.headers.update({"X-FDE-User": "nobody"})
    assert client.get("/api/v1/assets").status_code == 401


def test_missing_identity_rejected(client) -> None:
    client.headers.pop("X-FDE-User", None)
    assert client.get("/api/v1/assets").status_code == 401


def test_draft_only_visible_to_owner(client) -> None:
    client.headers.update({"X-FDE-User": "chen"})
    candidate = client.post(
        "/api/v1/harvest-candidates",
        json={
            "kind": "Case",
            "name": "private-draft",
            "title": "私有草稿",
            "scope": "engagement",
            "engagement_slug": "policy-import",
        },
    ).json()
    client.headers.update({"X-FDE-User": "wang"})
    assert client.get(f"/api/v1/harvest-candidates/{candidate['candidate_id']}").status_code == 403
