"""可见性判定：全部下推到 SQL，避免应用层过滤导致分页数错乱。"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import and_, exists, or_, true
from sqlalchemy.sql.elements import ColumnElement

from fde_asset.core.db import asset_audiences, assets
from fde_asset.platform.identity import Principal


def audience_clause(
    *,
    department_codes: Iterable[str] = (),
    customer_codes: Iterable[str] = (),
    engagement_slugs: Iterable[str] = (),
    user_id: str = "",
) -> ColumnElement[bool]:
    """资产的可见范围里，有没有哪一项覆盖到给定的这些部门、客户、项目、个人。

    全公司可见的始终算覆盖到。一份资产可以有多项可见范围，任意一项对上就行。
    """
    matches = [asset_audiences.c.audience == "company"]
    departments = sorted(code for code in department_codes if code)
    if departments:
        matches.append(
            and_(
                asset_audiences.c.audience == "department", asset_audiences.c.code.in_(departments)
            )
        )
    customers = sorted(code for code in customer_codes if code)
    if customers:
        # 同一客户的多个项目之间复用最密集，而且常常跨部门
        matches.append(
            and_(asset_audiences.c.audience == "customer", asset_audiences.c.code.in_(customers))
        )
    slugs = sorted(code for code in engagement_slugs if code)
    if slugs:
        matches.append(
            and_(asset_audiences.c.audience == "engagement", asset_audiences.c.code.in_(slugs))
        )
    if user_id:
        matches.append(
            and_(asset_audiences.c.audience == "user", asset_audiences.c.code == user_id)
        )
    return exists().where(asset_audiences.c.asset_id == assets.c.asset_id, or_(*matches))


def visibility_clause(principal: Principal) -> ColumnElement[bool]:
    """公司级全员可见；部门、客户、项目看成员关系；点名给个人的看本人；受限资产单独判定。"""
    visible = audience_clause(
        department_codes=principal.department_codes,
        customer_codes=principal.customer_codes,
        engagement_slugs=principal.engagement_slugs,
        user_id=principal.user_id,
    )
    if principal.is_admin:
        visible = true()

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
    if scope == "user":
        # 个人专用的技能带脚本时要人看：本部门的资产评审员或部门主管（department_code 是作者的部门）
        same_department = bool(department_code) and principal.department_code == department_code
        return (
            same_department and (principal.is_asset_reviewer or principal.is_department_head)
        ) or (principal.is_asset_reviewer and not department_code)
    return False
