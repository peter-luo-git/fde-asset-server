"""身份与成员关系。

两种来源，共用同一个 Principal：

- 本地开发（LocalDirectory）：名单文件 + 请求头，无依赖跑通全链路。
- 和平台共用账号（PlatformDirectory）：登录只在平台做，这里拿浏览器带来的平台会话去问
  fde-server「这是谁」；用户、部门、项目成员关系也都从 fde-server 读。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Membership:
    engagement_slug: str
    department_code: str
    role: str = "member"  # owner | member | viewer
    #: 这个项目是哪个客户的；同一客户的多个项目之间复用最密集
    customer_code: str = ""


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

    @property
    def customer_codes(self) -> set[str]:
        """我参与过的项目所属的客户；客户级资产按这个放行。"""
        return {m.customer_code for m in self.memberships if m.customer_code}

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
                    customer_code=m.get("customer_code", ""),
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
                    m["engagement_slug"],
                    m.get("department_code", ""),
                    m.get("role", "member"),
                    m.get("customer_code", ""),
                )
                for m in payload.get("memberships", [])
            ),
        )
        self._cache[user_id] = (now, principal)
        return principal


class PlatformUnavailable(RuntimeError):
    """问不到 fde-server。身份相关的一律 fail closed：宁可报错，不放行。"""


def _principal_from_platform(raw: dict) -> Principal:
    department = raw.get("department") or {}
    return Principal(
        # 资产里记的负责人、评审人、订阅人都是账号名，所以这里用账号名做身份
        user_id=raw["username"],
        display_name=raw.get("display_name") or raw["username"],
        department_code=department.get("code", ""),
        is_admin=raw.get("system_role") == "admin",
        is_asset_reviewer=bool(raw.get("is_asset_reviewer")),
        is_department_head=bool(raw.get("is_department_head")),
        memberships=tuple(
            Membership(
                engagement_slug=m["engagement_slug"],
                department_code=m.get("department_code", ""),
                role=m.get("role", "member"),
                customer_code=m.get("customer_code", ""),
            )
            for m in raw.get("memberships", [])
        ),
    )


class PlatformDirectory:
    """和平台共用一套账号：身份和名单都来自 fde-server，这里只做短时间缓存。

    - `resolve_session`：一次浏览器请求是谁——把平台的登录会话转给 fde-server 去认。
    - `users` / `resolve` / `engagements`：没有浏览器参与时也要用的名单（挑评审人、
      发通知、后台任务），凭共享密钥读。
    - Agent 清单平台那边还没有对应的接口，暂时仍读本地名单文件。
    """

    SESSION_COOKIE = "fde_session"

    def __init__(
        self,
        base_url: str,
        service_key: str,
        *,
        ttl_seconds: int = 30,
        client=None,
        local: LocalDirectory | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._service_key = service_key
        self.ttl = ttl_seconds
        self._client = client
        self._local = local
        self._lock = threading.Lock()
        self._snapshot: tuple[float, dict] | None = None
        self._sessions: dict[str, tuple[float, Principal]] = {}

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=5.0)
        return self._client

    def _get(self, path: str, **kwargs):
        try:
            return self._http().get(self.base_url + path, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 网络层的各种失败都算「问不到」
            raise PlatformUnavailable(str(exc)) from None

    # ---- 这次请求是谁 ----

    def resolve_session(self, session_cookie: str | None) -> Principal:
        if not session_cookie:
            raise PrincipalNotFound("未登录")
        key = hashlib.sha256(session_cookie.encode()).hexdigest()
        now = time.monotonic()
        with self._lock:
            hit = self._sessions.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        response = self._get(
            "/internal/identity/principal", cookies={self.SESSION_COOKIE: session_cookie}
        )
        if response.status_code in (401, 403):
            with self._lock:
                self._sessions.pop(key, None)
            raise PrincipalNotFound("登录已失效")
        if response.status_code != 200:
            raise PlatformUnavailable(f"fde-server 返回 {response.status_code}")
        principal = _principal_from_platform(response.json())
        with self._lock:
            # 会话会不断产生新的，顺手清掉过期的，别让缓存只增不减
            self._sessions = {k: v for k, v in self._sessions.items() if now - v[0] < self.ttl}
            self._sessions[key] = (now, principal)
        return principal

    # ---- 名单 ----

    def _directory(self) -> dict:
        now = time.monotonic()
        with self._lock:
            if self._snapshot and now - self._snapshot[0] < self.ttl:
                return self._snapshot[1]
        if not self._service_key:
            raise PlatformUnavailable("没有配置和 fde-server 共用的服务密钥")
        response = self._get(
            "/internal/identity/directory", headers={"X-FDE-Service-Key": self._service_key}
        )
        if response.status_code != 200:
            raise PlatformUnavailable(f"fde-server 返回 {response.status_code}")
        payload = response.json()
        snapshot = {
            "users": {raw["username"]: _principal_from_platform(raw) for raw in payload["users"]},
            "engagements": {
                raw["slug"]: {
                    "title": raw.get("name") or raw["slug"],
                    "department_code": raw.get("department_code", ""),
                    "customer_code": raw.get("customer_code", ""),
                    "owner": raw.get("owner", ""),
                    "industry": raw.get("industry", ""),
                    "stage": raw.get("stage", ""),
                    "description": raw.get("description", ""),
                }
                for raw in payload["engagements"]
            },
        }
        with self._lock:
            self._snapshot = (now, snapshot)
        return snapshot

    def users(self) -> list[str]:
        return sorted(self._directory()["users"])

    def resolve(self, user_id: str) -> Principal:
        principal = self._directory()["users"].get(user_id)
        if principal is None:
            raise PrincipalNotFound(user_id)
        return principal

    def engagements(self) -> dict[str, dict]:
        return self._directory()["engagements"]

    def agents(self) -> dict[str, dict]:
        return self._local.agents() if self._local is not None else {}
