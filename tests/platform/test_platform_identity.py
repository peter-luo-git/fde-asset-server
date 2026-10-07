"""和平台共用账号：身份看平台的登录会话，名单读 fde-server，不认调用方自己报的身份。"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from fde_asset.api.app import create_app
from fde_asset.platform.identity import (
    PlatformDirectory,
    PlatformUnavailable,
    PrincipalNotFound,
)

WANG = {
    "user_id": "u-1",
    "username": "wang",
    "display_name": "老王",
    "system_role": "member",
    "is_asset_reviewer": False,
    "is_department_head": True,
    "department": {"code": "finance", "name": "金融事业部"},
    "memberships": [
        {
            "engagement_slug": "policy-import",
            "role": "owner",
            "department_code": "finance",
            "customer_code": "HUAAN",
        }
    ],
}
ADMIN = {
    "user_id": "u-2",
    "username": "admin",
    "display_name": "管理员",
    "system_role": "admin",
    "is_asset_reviewer": True,
    "is_department_head": False,
    "department": None,
    "memberships": [],
}
DIRECTORY = {
    "users": [WANG, ADMIN],
    "engagements": [
        {
            "slug": "policy-import",
            "name": "华安人寿保单批量导入",
            "description": "",
            "stage": "poc",
            "department_code": "finance",
            "customer_code": "HUAAN",
            "industry": "insurance",
            "owner": "wang",
        }
    ],
}


class _Response:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakePlatform:
    """假的 fde-server：按会话返回人，按服务密钥返回名单，并记下被问了几次。"""

    def __init__(self, sessions: dict[str, dict], service_key: str = "k" * 32) -> None:
        self.sessions = sessions
        self.service_key = service_key
        self.calls: list[str] = []
        self.down = False

    def get(self, url: str, *, cookies=None, headers=None):
        if self.down:
            raise ConnectionError("fde-server 连不上")
        path = url.split("//", 1)[-1].split("/", 1)[-1]
        self.calls.append(path)
        if path == "internal/identity/principal":
            raw = self.sessions.get((cookies or {}).get("fde_session", ""))
            return _Response(200, raw) if raw else _Response(401)
        if path == "internal/identity/directory":
            if (headers or {}).get("X-FDE-Service-Key") != self.service_key:
                return _Response(404)
            return _Response(200, DIRECTORY)
        return _Response(404)


def directory(platform: FakePlatform, **kwargs) -> PlatformDirectory:
    kwargs.setdefault("service_key", platform.service_key)
    service_key = kwargs.pop("service_key")
    return PlatformDirectory("http://server-api:8000", service_key, client=platform, **kwargs)


def test_session_becomes_a_principal_keyed_by_username() -> None:
    source = directory(FakePlatform({"cookie-wang": WANG}))
    principal = source.resolve_session("cookie-wang")

    assert principal.user_id == "wang" and principal.display_name == "老王"
    assert principal.department_code == "finance"
    assert principal.is_department_head and not principal.is_admin
    assert principal.owns_engagement("policy-import")
    assert principal.customer_codes == {"HUAAN"}


def test_admin_comes_from_the_platform_system_role() -> None:
    principal = directory(FakePlatform({"c": ADMIN})).resolve_session("c")
    assert principal.is_admin and principal.is_asset_reviewer and principal.department_code == ""


def test_missing_or_rejected_session_is_not_a_user() -> None:
    source = directory(FakePlatform({"cookie-wang": WANG}))
    with pytest.raises(PrincipalNotFound):
        source.resolve_session(None)
    with pytest.raises(PrincipalNotFound):
        source.resolve_session("someone-elses-old-cookie")


def test_session_is_cached_briefly_then_asked_again() -> None:
    platform = FakePlatform({"cookie-wang": WANG})
    source = directory(platform, ttl_seconds=60)
    source.resolve_session("cookie-wang")
    source.resolve_session("cookie-wang")
    assert platform.calls.count("internal/identity/principal") == 1

    fresh = directory(platform, ttl_seconds=0)
    fresh.resolve_session("cookie-wang")
    fresh.resolve_session("cookie-wang")
    assert platform.calls.count("internal/identity/principal") == 3


def test_platform_down_fails_closed() -> None:
    platform = FakePlatform({"cookie-wang": WANG})
    source = directory(platform, ttl_seconds=0)
    platform.down = True
    with pytest.raises(PlatformUnavailable):
        source.resolve_session("cookie-wang")
    with pytest.raises(PlatformUnavailable):
        source.users()


def test_directory_lists_users_and_projects() -> None:
    source = directory(FakePlatform({}))
    assert source.users() == ["admin", "wang"]
    assert source.resolve("wang").is_department_head
    assert source.engagements()["policy-import"] == {
        "title": "华安人寿保单批量导入",
        "department_code": "finance",
        "customer_code": "HUAAN",
        "owner": "wang",
        "industry": "insurance",
        "stage": "poc",
        "description": "",
    }
    with pytest.raises(PrincipalNotFound):
        source.resolve("nobody")


def test_directory_needs_the_shared_service_key() -> None:
    platform = FakePlatform({})
    with pytest.raises(PlatformUnavailable):
        directory(platform, service_key="").users()
    with pytest.raises(PlatformUnavailable):
        directory(platform, service_key="wrong-key").users()


def test_api_trusts_the_session_not_the_header(seeded) -> None:
    """平台模式下，接口只认登录会话；自己在请求头里报一个管理员没有用。"""
    app = create_app(seeded.model_copy(update={"identity_mode": "platform"}))
    with TestClient(app) as client:
        app.state.context.directory = directory(FakePlatform({"cookie-wang": WANG}))

        forged = client.get("/api/v1/assets", headers={"X-FDE-User": "admin"})
        assert forged.status_code == 401

        client.cookies.set("fde_session", "cookie-wang")
        listed = client.get("/api/v1/assets", headers={"X-FDE-User": "admin"})
        assert listed.status_code == 200, listed.text
        # 演示身份切换在这个模式下不存在
        assert client.get("/api/v1/dev/identities").status_code == 404


def test_api_reports_platform_outage_as_unavailable(seeded) -> None:
    app = create_app(seeded.model_copy(update={"identity_mode": "platform"}))
    with TestClient(app) as client:
        platform = FakePlatform({"cookie-wang": WANG})
        app.state.context.directory = directory(platform, ttl_seconds=0)
        platform.down = True
        client.cookies.set("fde_session", "cookie-wang")
        assert client.get("/api/v1/assets").status_code == 503
