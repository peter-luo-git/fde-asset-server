"""身份与成员关系。

正式环境只有一套用户（Casdoor）：资产服务自己校验 OIDC 令牌，成员关系向 fde-server 查询。
本地开发用目录文件 + 请求头，便于无依赖跑通全链路。两种实现共用同一个 Principal。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Membership:
    engagement_slug: str
    department_code: str
    role: str = "member"  # owner | member | viewer


@dataclass(frozen=True)
class Principal:
    """一次请求的身份视图。资产服务只做业务授权，不管账号与口令。"""

    user_id: str
    display_name: str = ""
    department_code: str = ""
    is_admin: bool = False
    is_asset_reviewer: bool = False
    is_department_head: bool = False
    memberships: tuple[Membership, ...] = field(default_factory=tuple)

    @property
    def engagement_slugs(self) -> set[str]:
        return {m.engagement_slug for m in self.memberships}

    @property
    def department_codes(self) -> set[str]:
        """可见部门 = 主部门 + 参与项目所属部门（跨部门支援者也能看到该部门资产）。"""
        codes = {m.department_code for m in self.memberships if m.department_code}
        if self.department_code:
            codes.add(self.department_code)
        return codes

    def owns_engagement(self, slug: str) -> bool:
        return any(m.engagement_slug == slug and m.role == "owner" for m in self.memberships)


class PrincipalNotFound(LookupError):
    pass


class DirectorySource(Protocol):
    def resolve(self, user_id: str) -> Principal: ...


class LocalDirectory:
    """开发用目录：一个 JSON 文件描述用户、部门与项目成员关系。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"users": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def resolve(self, user_id: str) -> Principal:
        users = self._load().get("users", {})
        raw = users.get(user_id)
        if raw is None:
            raise PrincipalNotFound(user_id)
        return Principal(
            user_id=user_id,
            display_name=raw.get("display_name", user_id),
            department_code=raw.get("department_code", ""),
            is_admin=bool(raw.get("is_admin")),
            is_asset_reviewer=bool(raw.get("is_asset_reviewer")),
            is_department_head=bool(raw.get("is_department_head")),
            memberships=tuple(
                Membership(
                    engagement_slug=m["engagement_slug"],
                    department_code=m.get("department_code", ""),
                    role=m.get("role", "member"),
                )
                for m in raw.get("memberships", [])
            ),
        )

    def users(self) -> list[str]:
        """开发目录里的全部用户，用来算某个作用域下谁有评审权。"""
        return sorted(self._load().get("users", {}))

    def engagements(self) -> dict[str, dict]:
        """项目清单：推荐要按项目上下文匹配，正式环境由 fde-server 提供。"""
        return self._load().get("engagements", {})

    def agents(self) -> dict[str, dict]:
        """Agent 清单（按预设 key 索引），同上。"""
        return self._load().get("agents", {})

    def write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class HttpDirectory:
    """正式环境：成员关系来自 fde-server 的只读接口，带缓存与 fail closed。

    资产服务不信任调用方传来的用户头，用户身份由 OIDC 令牌确定（见 OidcVerifier）。
    """

    def __init__(self, base_url: str, ttl_seconds: int = 60, client=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.ttl = ttl_seconds
        self._cache: dict[str, tuple[float, Principal]] = {}
        self._client = client

    def users(self) -> list[str]:  # pragma: no cover - 正式环境由 fde-server 提供名单
        """正式环境不从资产服务枚举用户；评审人名单由 fde-server 给。"""
        return []

    def engagements(self) -> dict[str, dict]:  # pragma: no cover - 同上
        return {}

    def agents(self) -> dict[str, dict]:  # pragma: no cover - 同上
        return {}

    def resolve(self, user_id: str) -> Principal:
        now = time.monotonic()
        hit = self._cache.get(user_id)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        if self._client is None:  # pragma: no cover - 需要真实 fde-server
            raise PrincipalNotFound(user_id)
        response = self._client.get(f"{self.base_url}/internal/v1/users/{user_id}/memberships")
        response.raise_for_status()
        payload = response.json()
        principal = Principal(
            user_id=user_id,
            display_name=payload.get("display_name", user_id),
            department_code=payload.get("department_code", ""),
            is_admin=bool(payload.get("is_admin")),
            is_asset_reviewer=bool(payload.get("is_asset_reviewer")),
            is_department_head=bool(payload.get("is_department_head")),
            memberships=tuple(
                Membership(
                    m["engagement_slug"], m.get("department_code", ""), m.get("role", "member")
                )
                for m in payload.get("memberships", [])
            ),
        )
        self._cache[user_id] = (now, principal)
        return principal
