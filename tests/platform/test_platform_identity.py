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
    "agents": [
        {
            "key": "platform-coder",
            "title": "平台开发",
            "description": "导入与超时处理",
            "role": "coding_agent",
            "owner": "wang",
            "department_code": "finance",
        },
        {
            "key": "coder-default",
            "title": "阿码",
            "description": "通用开发",
            "role": "coding_agent",
            "owner": "",
            "department_code": "",
        },
    ],
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
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class FakePlatform:
    """假的 fde-server：按会话返回人，按服务密钥返回名单，并记下被问了几次。"""

    def __init__(self, sessions: dict[str, dict], service_key: str = "k" * 32) -> None:
        self.sessions = sessions
        self.service_key = service_key
        self.calls: list[str] = []
        self.down = False
        self.directory_payload = DIRECTORY
        self.failure_message = "fde-server 连不上"

    def get(self, url: str, *, cookies=None, headers=None):
        if self.down:
            raise ConnectionError(self.failure_message)
        path = url.split("//", 1)[-1].split("/", 1)[-1]
        self.calls.append(path)
        if path == "internal/identity/principal":
            raw = self.sessions.get((cookies or {}).get("fde_session", ""))
            return _Response(200, raw) if raw else _Response(401)
        if path == "internal/identity/directory":
            if (headers or {}).get("X-FDE-Service-Key") != self.service_key:
                return _Response(404)
            return _Response(200, self.directory_payload)
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


def test_agents_use_the_authenticated_platform_snapshot_and_safe_fields(tmp_path) -> None:
    from fde_asset.platform.identity import LocalDirectory

    local = LocalDirectory(tmp_path / "directory.json")
    local.write({"agents": {"demo-agent": {"owner": "wang"}}})
    platform = FakePlatform({})
    platform.directory_payload = {
        **DIRECTORY,
        "agents": [
            {**DIRECTORY["agents"][0], "model_alias": "private", "instructions_md": "secret"}
        ],
    }
    source = directory(platform, local=local)
    assert source.agents() == {
        "platform-coder": {
            "title": "平台开发",
            "description": "导入与超时处理",
            "role": "coding_agent",
            "owner": "wang",
            "department_code": "finance",
            # 和项目、人的关系：老版本平台没给就是空
            "scope": "",
            "engagement_slug": "",
            "engagement_name": "",
            "sharing_mode": "",
        }
    }
    assert source.users() == ["admin", "wang"]
    assert platform.calls == ["internal/identity/directory"]
    with pytest.raises(PlatformUnavailable):
        directory(platform, service_key="wrong-key", local=local).agents()


def test_expired_agent_snapshot_fails_closed_and_refreshes(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("fde_asset.platform.identity.time.monotonic", lambda: now[0])
    platform = FakePlatform({})
    source = directory(platform, ttl_seconds=30)
    assert "platform-coder" in source.agents()
    platform.directory_payload = {**DIRECTORY, "agents": []}
    now[0] = 129.0
    assert "platform-coder" in source.agents()
    now[0] = 130.0
    platform.down = True
    with pytest.raises(PlatformUnavailable):
        source.agents()
    platform.down = False
    assert source.agents() == {}


@pytest.mark.parametrize("agents", [None, {}, [{"title": "missing-key"}]])
def test_missing_or_invalid_agent_contract_is_an_explicit_dependency_failure(agents) -> None:
    platform = FakePlatform({})
    platform.directory_payload = {**DIRECTORY}
    if agents is None:
        platform.directory_payload.pop("agents")
    else:
        platform.directory_payload["agents"] = agents
    with pytest.raises(PlatformUnavailable, match="Agent"):
        directory(platform).agents()


def test_platform_recommend_targets_use_real_agent_owners_and_builtin_admin_management(
    seeded,
) -> None:
    app = create_app(seeded.model_copy(update={"identity_mode": "platform"}))
    with TestClient(app) as client:
        app.state.context.directory = directory(FakePlatform({"wang": WANG, "admin": ADMIN}))
        client.cookies.set("fde_session", "wang")
        body = client.get("/api/v1/recommend/targets").json()
        mine = {row["target_id"]: row for row in body["mine"] if row["target_type"] == "agent"}
        assert set(mine) == {"platform-coder"}
        assert mine["platform-coder"]["owner_name"] == "老王"
        assert mine["platform-coder"]["role"] == "coding_agent"
        assert mine["platform-coder"]["description"] == "导入与超时处理"
        assert (
            client.post(
                "/api/v1/recommend/link",
                json={
                    "target_type": "agent",
                    "target_id": "coder-default",
                    "asset_id": "any-asset",
                },
            ).status_code
            == 403
        )
        client.cookies.set("fde_session", "admin")
        body = client.get("/api/v1/recommend/targets").json()
        assert {row["target_id"] for row in body["mine"] if row["target_type"] == "agent"} == {
            "platform-coder",
            "coder-default",
        }


def test_recommend_targets_report_old_server_agent_contract_as_unavailable(seeded) -> None:
    app = create_app(seeded.model_copy(update={"identity_mode": "platform"}))
    with TestClient(app, raise_server_exceptions=False) as client:
        platform = FakePlatform({"wang": WANG})
        platform.directory_payload = {
            key: value for key, value in DIRECTORY.items() if key != "agents"
        }
        app.state.context.directory = directory(platform)
        client.cookies.set("fde_session", "wang")
        assert client.get("/api/v1/recommend/targets").status_code == 503


def test_recommend_directory_outage_is_503_without_internal_error_details(seeded) -> None:
    app = create_app(seeded.model_copy(update={"identity_mode": "platform"}))
    with TestClient(app, raise_server_exceptions=False) as client:
        platform = FakePlatform({"wang": WANG})
        source = directory(platform)
        # Authentication remains valid in its short cache while a new directory
        # request fails; this exercises the route failure, not auth handling.
        source.resolve_session("wang")
        platform.down = True
        platform.failure_message = "http://internal-server/?service_key=private-service-secret"
        app.state.context.directory = source
        client.cookies.set("fde_session", "wang")
        response = client.get("/api/v1/recommend/targets")
        assert response.status_code == 503
        assert "private-service-secret" not in response.text
        assert "internal-server" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        ValueError("private-response-data"),
        {"agents": [], "users": None, "engagements": []},
        {"agents": [], "users": [{"display_name": "missing-username"}], "engagements": []},
    ],
)
def test_malformed_platform_directory_is_a_safe_dependency_failure(payload) -> None:
    platform = FakePlatform({})
    platform.directory_payload = payload
    with pytest.raises(PlatformUnavailable, match="目录") as caught:
        directory(platform).agents()
    assert "private-response-data" not in str(caught.value)


@pytest.mark.parametrize("empty_department", [True, False])
def test_department_head_cannot_manage_unowned_builtin_or_spoof_target_department(
    seeded, empty_department
) -> None:
    head = {**WANG, "department": None} if empty_department else WANG
    platform = FakePlatform({"head": head})
    target = {"target_type": "agent", "target_id": "coder-default"}
    if not empty_department:
        platform.directory_payload = {
            **DIRECTORY,
            "agents": [
                {
                    "key": "other-department-coder",
                    "title": "其他部门Agent",
                    "description": "",
                    "role": "coding_agent",
                    "owner": "admin",
                    "department_code": "engineering",
                }
            ],
        }
        target = {
            "target_type": "agent",
            "target_id": "other-department-coder",
            "department_code": "finance",
        }
    app = create_app(seeded.model_copy(update={"identity_mode": "platform"}))
    with TestClient(app) as client:
        app.state.context.directory = directory(platform)
        client.cookies.set("fde_session", "head")
        if empty_department:
            targets = client.get("/api/v1/recommend/targets").json()
            assert not [row for row in targets["department"] if row["target_type"] == "agent"]
        # Empty items would return 422 after successful permission checking;
        # this must be rejected at the authorization boundary first.
        response = client.post("/api/v1/recommend/send", json={**target, "items": []})
        assert response.status_code == 403
