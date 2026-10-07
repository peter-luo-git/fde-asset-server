"""给平台用的内部接口：某个用户的 Agent 能用哪些资产。"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
import pytest

from fde_asset.api.app import create_app
from fde_asset.modules.asset.indexer import index_all

KEY = "shared-service-key-for-tests-0123456789"


@pytest.fixture()
def service(seeded):
    app = create_app(seeded.model_copy(update={"server_service_key": KEY}))
    with TestClient(app) as client:
        context = app.state.context
        index_all(
            context.engine,
            context.repo_port,
            context.repos(),
            text_limit=context.settings.index_text_limit,
        )
        yield client


def catalog(client: TestClient, **params):
    return client.get(
        "/internal/v1/agent-catalog", params=params, headers={"X-FDE-Service-Key": KEY}
    )


def test_needs_the_shared_service_key(service, seeded) -> None:
    assert service.get("/internal/v1/agent-catalog", params={"username": "chen"}).status_code == 404
    wrong = service.get(
        "/internal/v1/agent-catalog",
        params={"username": "chen"},
        headers={"X-FDE-Service-Key": "wrong"},
    )
    assert wrong.status_code == 404
    # 用户身份头不能代替服务密钥
    forged = service.get(
        "/internal/v1/agent-catalog",
        params={"username": "chen"},
        headers={"X-FDE-User": "admin"},
    )
    assert forged.status_code == 404
    # 没配密钥的服务，这个接口等于不存在
    with TestClient(create_app(seeded)) as off:
        assert (
            off.get(
                "/internal/v1/agent-catalog",
                params={"username": "chen"},
                headers={"X-FDE-Service-Key": ""},
            ).status_code
            == 404
        )


def test_member_gets_company_and_own_project_assets_only(service) -> None:
    items = catalog(service, username="chen").json()["items"]

    assert {item["scope"] for item in items} == {"company", "engagement"}
    assert {item["kind"] for item in items} <= {"Skill", "Case", "Rule"}
    # 部门级、客户级的不给 Agent
    assert all(item["scope"] in ("company", "engagement") for item in items)
    project = [item for item in items if item["scope"] == "engagement"]
    assert project and {item["engagement_slug"] for item in project} == {"policy-import"}

    skill = next(item for item in items if item["name"] == "insurance-policy-import")
    assert skill["main_file"] == "SKILL.md" and skill["path"] == "skills/insurance-policy-import"
    assert len(skill["commit_sha"]) == 40 and len(skill["content_sha256"]) == 64
    assert skill["byte_size"] > 0 and skill["title"]


def test_outsider_gets_company_assets_only(service) -> None:
    items = catalog(service, username="zhao").json()["items"]
    assert items and {item["scope"] for item in items} == {"company"}
    # 点名一个自己不在的项目，也拿不到那个项目的资产
    named = catalog(service, username="zhao", engagement_slug="policy-import").json()["items"]
    assert {item["scope"] for item in named} == {"company"}


def test_naming_a_project_narrows_to_it(service) -> None:
    items = catalog(service, username="chen", engagement_slug="policy-import").json()["items"]
    assert {i["engagement_slug"] for i in items if i["scope"] == "engagement"} == {"policy-import"}
    other = catalog(service, username="chen", engagement_slug="core-migration").json()["items"]
    assert {item["scope"] for item in other} == {"company"}


def test_hash_and_size_describe_the_main_file(service) -> None:
    app_context = service.app.state.context
    skill = next(
        item
        for item in catalog(service, username="chen").json()["items"]
        if item["name"] == "insurance-policy-import"
    )
    repo = next(r for r in app_context.repos() if r.name == skill["repo"])
    content = app_context.repo_port.read_file(repo, skill["path"] + "/SKILL.md")
    assert skill["content_sha256"] == hashlib.sha256(content).hexdigest()
    assert skill["byte_size"] == len(content)


def test_unknown_user_is_not_found_and_route_is_not_in_the_contract(service) -> None:
    assert catalog(service, username="nobody").status_code == 404
    assert "/internal/v1/agent-catalog" not in service.app.openapi()["paths"]
