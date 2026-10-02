"""可见性判定：全部下推到 SQL，避免应用层过滤导致分页数错乱。"""

from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from fde_asset.core.db import assets
from fde_asset.platform.identity import Principal


def visibility_clause(principal: Principal) -> ColumnElement[bool]:
    """公司级全员可见；部门级看部门集合；项目级看项目成员；受限资产单独判定。"""
    clauses = [assets.c.scope == "company"]

    departments = principal.department_codes
    if departments:
        clauses.append(
            and_(assets.c.scope == "department", assets.c.department_code.in_(departments))
        )

    customers = principal.customer_codes
    if customers:
        # 同一客户的多个项目之间复用最密集，而且常常跨部门
        clauses.append(and_(assets.c.scope == "customer", assets.c.customer_code.in_(customers)))

    slugs = principal.engagement_slugs
    if slugs:
        clauses.append(and_(assets.c.scope == "engagement", assets.c.engagement_slug.in_(slugs)))

    visible = or_(*clauses)
    if principal.is_admin:
        visible = or_(
            visible, assets.c.scope.in_(["company", "department", "customer", "engagement"])
        )

    # 受限资产：只有资产所有人与负责部门的部门主管可见，管理员也不例外
    restricted_ok = or_(
        assets.c.restricted.is_(False),
        and_(assets.c.owner_kind == "user", assets.c.owner_value == principal.user_id),
        and_(
            assets.c.owner_kind == "department",
            assets.c.owner_value == principal.department_code,
            principal.is_department_head,
        )
        if principal.is_department_head
        else assets.c.restricted.is_(False),
    )
    return and_(visible, restricted_ok, assets.c.deleted_at.is_(None))


def can_review(
    principal: Principal,
    scope: str,
    *,
    department_code: str = "",
    engagement_slug: str = "",
    customer_code: str = "",
) -> bool:
    """评审权限：项目级看项目 owner，部门级看本部门评审员或主管，客户级看资产评审员或该客户项目负责人，公司级看资产评审员。"""
    if principal.is_admin:
        return True
    if scope == "engagement":
        return principal.owns_engagement(engagement_slug)
    if scope == "department":
        same_department = principal.department_code == department_code
        return same_department and (principal.is_asset_reviewer or principal.is_department_head)
    if scope == "customer":
        # 客户级跨部门，由资产评审员把关；该客户下任一项目的负责人也可以
        if principal.is_asset_reviewer:
            return True
        return any(
            m.customer_code == customer_code and m.role == "owner" for m in principal.memberships
        )
    if scope == "company":
        return principal.is_asset_reviewer
    return False
