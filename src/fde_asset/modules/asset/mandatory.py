"""必选资产：在某个范围里干活的 Agent 一定带上的资产。

以前"必带"是按类型推导的：规范和流程在可见范围内一律带。资产是大家自己上传的，
上传的人不知道选了"规范"就等于强制给所有 Agent，规范和流程也不一定普适，
所以改成由人指定（2026-10-17）：

- 范围：全公司、部门、客户、项目、个人；
- 谁能设：管理员，以及这个范围的负责人——部门主管管本部门，客户下任一项目的负责人管这个客户，
  项目负责人管本项目，个人级由本人设；
- 个人级只在这个人和 Agent 的私聊里生效：共享会话里换人说话不会换资产包，
  一个人的个人资料也不会经 Agent 的回复让别人看到；
- 设的时候资产必须对这个范围可见、没被删、校验通过、不是受限资产。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from fde_asset.core.db import asset_audiences, asset_mandatory, assets
from fde_asset.modules.asset.visibility import audience_clause
from fde_asset.platform.identity import Principal

SCOPES = ("company", "department", "customer", "engagement", "user")
#: 这几类不进 Agent 的资产包，设成必选也没用
NOT_FOR_AGENT = {"Application"}
#: SOP 也不进资产包（只用于执行任务）；以前设成必选的 SOP 保留记录但不再生效
INERT_KINDS = {"Sop"}


class MandatoryError(ValueError):
    """设不了：原因直接给页面看。"""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Entry:
    asset_id: str
    scope: str
    code: str
    created_by: str = ""
    created_at: Any = None

    @property
    def key(self) -> str:
        """和可见范围同一种写法：company、department:dev 这样"""
        return "company" if self.scope == "company" else f"{self.scope}:{self.code}"


def parse(entry: str) -> tuple[str, str]:
    """'company' / 'department:dev' / 'user:alice' → (范围, 代号)"""
    scope, _, code = str(entry or "").strip().partition(":")
    scope, code = scope.strip(), code.strip()
    if scope not in SCOPES:
        raise MandatoryError(f"范围只能是 {'、'.join(SCOPES)}")
    if scope == "company":
        if code:
            raise MandatoryError("全公司不用填代号")
        return scope, ""
    if not code or len(code) > 64:
        raise MandatoryError("要填部门代号、客户代号、项目标识或账号")
    return scope, code


def may_manage(principal: Principal, scope: str, code: str) -> bool:
    """管理员，或者这个范围的负责人。"""
    if principal.is_admin:
        return True
    if scope == "department":
        return principal.is_department_head and principal.department_code == code
    if scope == "customer":
        return any(m.customer_code == code and m.role == "owner" for m in principal.memberships)
    if scope == "engagement":
        return principal.owns_engagement(code)
    if scope == "user":
        return principal.user_id == code
    return False  # 全公司只有管理员


def manageable_scopes(principal: Principal) -> list[str]:
    """这个人能给哪些范围设必选，页面下拉用。管理员不列具体代号，什么都能填。"""
    if principal.is_admin:
        return ["company", "department", "customer", "engagement", "user"]
    keys: list[str] = []
    if principal.is_department_head and principal.department_code:
        keys.append(f"department:{principal.department_code}")
    keys += sorted(
        {
            f"customer:{m.customer_code}"
            for m in principal.memberships
            if m.role == "owner" and m.customer_code
        }
    )
    keys += sorted(
        {f"engagement:{m.engagement_slug}" for m in principal.memberships if m.role == "owner"}
    )
    keys.append(f"user:{principal.user_id}")
    return keys


def _visible_in(
    conn: Connection, asset_id: str, scope: str, code: str, context: dict[str, str]
) -> bool:
    """资产对这个范围可见吗。项目和个人要把它所属的部门、客户一起算上。"""
    if scope == "company":
        # 全公司必选要求全公司可见，不能靠"在某个部门可见"凑
        return (
            conn.execute(
                select(asset_audiences.c.id).where(
                    asset_audiences.c.asset_id == asset_id, asset_audiences.c.audience == "company"
                )
            ).first()
            is not None
        )
    if scope == "department":
        clause = audience_clause(department_codes=[code])
    elif scope == "customer":
        clause = audience_clause(customer_codes=[code])
    elif scope == "engagement":
        clause = audience_clause(
            department_codes=[context.get("department_code", "")],
            customer_codes=[context.get("customer_code", "")],
            engagement_slugs=[code],
        )
    else:
        clause = audience_clause(
            department_codes=context.get("department_codes", "").split(","),
            customer_codes=context.get("customer_codes", "").split(","),
            engagement_slugs=context.get("engagement_slugs", "").split(","),
            user_id=code,
        )
    found = conn.execute(select(assets.c.asset_id).where(assets.c.asset_id == asset_id, clause))
    return found.first() is not None


def add(
    engine: Engine,
    principal: Principal,
    asset_id: str,
    entry: str,
    *,
    context: dict[str, str] | None = None,
) -> tuple[Entry, bool]:
    """设为必选，返回 (这一条, 是不是新加的)。`context` 给项目和个人补上所属的部门、客户。"""
    scope, code = parse(entry)
    if not may_manage(principal, scope, code):
        raise MandatoryError("只有管理员或这个范围的负责人能设必选资产", status=403)
    with engine.begin() as conn:
        row = conn.execute(select(assets).where(assets.c.asset_id == asset_id)).first()
        if row is None or row.deleted_at is not None:
            raise MandatoryError("资产不存在", status=404)
        if not row.valid:
            raise MandatoryError("这份资产没通过校验，先修好再设必选")
        if row.restricted:
            raise MandatoryError("受限资产不进 Agent 的资产包，不能设必选")
        if row.kind in NOT_FOR_AGENT:
            raise MandatoryError("应用不进 Agent 的资产包，不能设必选")
        if row.kind == "Sop":
            raise MandatoryError(
                "SOP 只用于按步骤执行任务，不进资产包，不能设必选；"
                "要让某个范围里的任务都按它执行，请设为默认流程"
            )
        if row.lifecycle in {"deprecated", "archived", "draft"}:
            raise MandatoryError("废弃、归档或草稿状态的资产不会带给 Agent，不能设必选")
        if not _visible_in(conn, asset_id, scope, code, context or {}):
            raise MandatoryError("这份资产对这个范围不可见，先调整可见范围")
        existing = conn.execute(
            select(asset_mandatory).where(
                asset_mandatory.c.asset_id == asset_id,
                asset_mandatory.c.scope == scope,
                asset_mandatory.c.code == code,
            )
        ).first()
        if existing is not None:
            return _entry(existing), False
        try:
            conn.execute(
                asset_mandatory.insert().values(
                    asset_id=asset_id, scope=scope, code=code, created_by=principal.user_id
                )
            )
        except IntegrityError:  # 两个人同时点
            pass
        saved = conn.execute(
            select(asset_mandatory).where(
                asset_mandatory.c.asset_id == asset_id,
                asset_mandatory.c.scope == scope,
                asset_mandatory.c.code == code,
            )
        ).first()
    return _entry(saved), True


def remove(engine: Engine, principal: Principal, asset_id: str, entry: str) -> bool:
    scope, code = parse(entry)
    if not may_manage(principal, scope, code):
        raise MandatoryError("只有管理员或这个范围的负责人能取消必选", status=403)
    with engine.begin() as conn:
        result = conn.execute(
            delete(asset_mandatory).where(
                asset_mandatory.c.asset_id == asset_id,
                asset_mandatory.c.scope == scope,
                asset_mandatory.c.code == code,
            )
        )
    return bool(result.rowcount)


def _entry(row: Any) -> Entry:
    return Entry(row.asset_id, row.scope, row.code, row.created_by, row.created_at)


def listing(
    engine: Engine, *, scopes: Iterable[tuple[str, str]] | None = None
) -> list[dict[str, Any]]:
    """必选资产清单（带资产标题和类型）；`scopes` 给了就只列这些范围的。"""
    query = (
        select(
            asset_mandatory,
            assets.c.title,
            assets.c.kind,
            assets.c.name,
            assets.c.valid,
            assets.c.deleted_at,
            assets.c.restricted,
            assets.c.lifecycle,
        )
        .select_from(
            asset_mandatory.outerjoin(assets, assets.c.asset_id == asset_mandatory.c.asset_id)
        )
        .order_by(asset_mandatory.c.scope, asset_mandatory.c.code, asset_mandatory.c.id)
    )
    if scopes is not None:
        wanted = list(scopes)
        if not wanted:
            return []
        query = query.where(
            or_(
                *[
                    and_(asset_mandatory.c.scope == s, asset_mandatory.c.code == c)
                    for s, c in wanted
                ]
            )
        )
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
    result = []
    for row in rows:
        entry = _entry(row)
        carried = bool(
            row.title is not None
            and row.deleted_at is None
            and row.valid
            and not row.restricted
            and row.lifecycle not in {"deprecated", "archived", "draft"}
            and row.kind not in INERT_KINDS
        )
        result.append(
            {
                "asset_id": entry.asset_id,
                "entry": entry.key,
                "scope": entry.scope,
                "code": entry.code,
                "title": row.title or "（资产已不存在）",
                "kind": row.kind or "",
                "name": row.name or "",
                "created_by": entry.created_by,
                "created_at": entry.created_at.isoformat() if entry.created_at else "",
                # 资产后来被废弃、删除、改成受限的，这条还在，但组包时不会带
                "effective": carried,
                **(
                    {"inert_reason": "SOP 已不再作为必选资产，请改为设默认流程"}
                    if row.kind in INERT_KINDS
                    else {}
                ),
            }
        )
    return result


def ids_for(
    conn: Connection,
    *,
    department_code: str = "",
    customer_code: str = "",
    engagement_slug: str = "",
    username: str = "",
) -> set[str]:
    """这次组包要必带哪些资产。`username` 只在私聊里给。"""
    matches = [asset_mandatory.c.scope == "company"]
    for scope, code in (
        ("department", department_code),
        ("customer", customer_code),
        ("engagement", engagement_slug),
        ("user", username),
    ):
        if code:
            matches.append(and_(asset_mandatory.c.scope == scope, asset_mandatory.c.code == code))
    return {
        row.asset_id
        for row in conn.execute(select(asset_mandatory.c.asset_id).where(or_(*matches)))
    }
