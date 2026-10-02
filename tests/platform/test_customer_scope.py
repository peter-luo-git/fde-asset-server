"""客户级作用域：同一个客户的多个项目之间复用最密集，而且常常跨部门。"""

from __future__ import annotations

from fde_asset.modules.asset.visibility import can_review
from fde_asset.platform.identity import Membership, Principal
from tests.conftest import as_user

HUAAN_MEMBER = Principal(
    "chen",
    department_code="data-intel",
    memberships=(
        Membership(
            engagement_slug="policy-import", department_code="finance", customer_code="HUAAN"
        ),
    ),
)
OUTSIDER = Principal("zhao", department_code="market")


def test_customer_assets_are_visible_to_that_customers_project_members(client) -> None:
    chen = as_user(client, "chen").get("/api/v1/assets", params={"limit": 100}).json()
    names = {item["name"] for item in chen["items"]}
    assert "huaan-gateway-throttling" in names, "参与了这家客户项目的人该看到客户级资产"
    customer_items = [item for item in chen["items"] if item["scope"] == "customer"]
    assert customer_items and customer_items[0]["customer_code"] == "HUAAN"


def test_outsider_cannot_see_customer_assets(client) -> None:
    zhao = as_user(client, "zhao").get("/api/v1/assets", params={"limit": 100}).json()
    assert all(item["scope"] != "customer" for item in zhao["items"])


def test_customer_scope_filter(client) -> None:
    filtered = (
        as_user(client, "chen")
        .get("/api/v1/assets", params={"scope": "customer", "limit": 100})
        .json()
    )
    assert filtered["total"] >= 1
    assert all(item["scope"] == "customer" for item in filtered["items"])


def test_customer_membership_derives_customer_codes() -> None:
    assert HUAAN_MEMBER.customer_codes == {"HUAAN"}
    assert OUTSIDER.customer_codes == set()


def test_review_of_customer_assets() -> None:
    """客户级跨部门，由资产评审员把关；该客户下项目的负责人也可以。"""
    reviewer = Principal("li", department_code="data-intel", is_asset_reviewer=True)
    owner = Principal(
        "wang",
        department_code="finance",
        memberships=(Membership("policy-import", "finance", role="owner", customer_code="HUAAN"),),
    )
    assert can_review(reviewer, "customer", customer_code="HUAAN") is True
    assert can_review(owner, "customer", customer_code="HUAAN") is True
    assert can_review(HUAAN_MEMBER, "customer", customer_code="HUAAN") is False, "普通成员不能评"
    assert can_review(owner, "customer", customer_code="OTHER") is False, "别的客户不行"


def test_admin_still_sees_everything(client) -> None:
    admin = as_user(client, "admin").get("/api/v1/assets", params={"limit": 100}).json()
    assert any(item["scope"] == "customer" for item in admin["items"])
