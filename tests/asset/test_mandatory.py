"""必选资产：由管理员和范围负责人指定，Agent 组包时一定带上；规范和流程不再按类型自动必带。"""

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from fde_asset.api.app import create_app
from fde_asset.core.db import asset_mandatory, assets
from fde_asset.modules.asset.indexer import index_all

KEY = "controlled-mandatory-service-key"


@pytest.fixture()
def service(seeded):
    with TestClient(create_app(seeded.model_copy(update={"server_service_key": KEY}))) as client:
        client.headers.update({"X-FDE-User": "admin"})
        ctx = client.app.state.context
        index_all(ctx.engine, ctx.repo_port, ctx.repos(), file_store=ctx.file_store)
        yield client


def _id(service, name):
    with service.app.state.context.engine.connect() as conn:
        return conn.execute(select(assets.c.asset_id).where(assets.c.name == name)).scalar_one()


def _bundle(service, **body):
    response = service.post(
        "/internal/v1/agent-bundle",
        json={"username": "chen", "engagement_slug": "policy-import", **body},
        headers={"X-FDE-Service-Key": KEY},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _names(payload):
    return {item["name"] for item in payload["assets"]}


def _as(user):
    return {"X-FDE-User": user}


def _clear(service):
    with service.app.state.context.engine.begin() as conn:
        conn.execute(delete(asset_mandatory))


def test_seed_keeps_company_rules_and_sops_mandatory_so_old_behaviour_holds(service):
    payload = _bundle(service)
    assert {"00-security-redlines", "10-engineering", "delivery-standard"} <= _names(payload)
    assert "rules/00-security-redlines.md" in payload["files"]


def test_rules_and_sops_are_not_carried_by_kind_any_more(service):
    _clear(service)
    payload = _bundle(service)
    assert not any(path.startswith("rules/") for path in payload["files"])
    assert "delivery-standard" not in _names(payload)

    # 管理员设成全公司必选，下一轮就带上了
    rule = _id(service, "00-security-redlines")
    response = service.post(f"/api/v1/assets/{rule}/mandatory", json={"entry": "company"})
    assert response.status_code == 200 and response.json() == {"entry": "company", "created": True}
    again = service.post(f"/api/v1/assets/{rule}/mandatory", json={"entry": "company"})
    assert again.json()["created"] is False
    payload = _bundle(service)
    assert "rules/00-security-redlines.md" in payload["files"]
    assert "10-engineering" not in _names(payload)

    # 取消以后又不带了
    assert service.post(
        f"/api/v1/assets/{rule}/mandatory/remove", json={"entry": "company"}
    ).json() == {"removed": True}
    assert "00-security-redlines" not in _names(_bundle(service))


def test_mandatory_sop_brings_the_layers_it_extends(service):
    _clear(service)
    l2 = _id(service, "bank-core-migration")
    assert (
        service.post(f"/api/v1/assets/{l2}/mandatory", json={"entry": "company"}).status_code == 200
    )
    assert {"bank-core-migration", "delivery-standard"} <= _names(_bundle(service))


def test_only_admin_or_the_scope_owner_can_set(service):
    _clear(service)
    rule = _id(service, "10-engineering")
    url = f"/api/v1/assets/{rule}/mandatory"
    # 全公司只有管理员
    assert service.post(url, json={"entry": "company"}, headers=_as("wang")).status_code == 403
    # 部门主管管本部门，不管别的部门
    assert (
        service.post(url, json={"entry": "department:finance"}, headers=_as("wang")).status_code
        == 200
    )
    assert (
        service.post(url, json={"entry": "department:market"}, headers=_as("wang")).status_code
        == 403
    )
    # 项目负责人管本项目；普通成员不行
    assert (
        service.post(
            url, json={"entry": "engagement:policy-import"}, headers=_as("wang")
        ).status_code
        == 200
    )
    assert (
        service.post(
            url, json={"entry": "engagement:policy-import"}, headers=_as("chen")
        ).status_code
        == 403
    )
    # 客户下项目的负责人管这个客户
    assert (
        service.post(url, json={"entry": "customer:HUAAN"}, headers=_as("wang")).status_code == 200
    )
    # 个人级只有本人（和管理员）
    assert service.post(url, json={"entry": "user:chen"}, headers=_as("chen")).status_code == 200
    assert service.post(url, json={"entry": "user:chen"}, headers=_as("wang")).status_code == 403
    # 取消也一样看权限
    assert (
        service.post(url + "/remove", json={"entry": "user:chen"}, headers=_as("wang")).status_code
        == 403
    )

    view = service.get(url, headers=_as("wang")).json()
    assert {e["entry"] for e in view["entries"]} == {
        "department:finance",
        "engagement:policy-import",
        "customer:HUAAN",
        "user:chen",
    }
    assert {e["entry"]: e["can_remove"] for e in view["entries"]}["user:chen"] is False
    assert "engagement:policy-import" in view["manageable"]
    # 治理页清单：非管理员只看自己负责的范围
    listed = service.get("/api/v1/mandatory", headers=_as("wang")).json()["items"]
    assert "user:chen" not in {item["entry"] for item in listed}
    assert len(service.get("/api/v1/mandatory").json()["items"]) == 4


def test_bad_entries_and_assets_that_cannot_reach_the_agent_are_refused(service):
    rule = _id(service, "10-engineering")
    url = f"/api/v1/assets/{rule}/mandatory"
    for entry in ("", "team:x", "company:x", "department:"):
        assert service.post(url, json={"entry": entry}).status_code == 422, entry
    assert service.post(url, json={"entry": "user:nobody"}).status_code == 422
    app = _id(service, next(iter(_names_of_kind(service, "Application"))))
    assert (
        service.post(f"/api/v1/assets/{app}/mandatory", json={"entry": "company"}).status_code
        == 422
    )
    assert (
        service.post("/api/v1/assets/missing/mandatory", json={"entry": "company"}).status_code
        == 404
    )


def _names_of_kind(service, kind):
    with service.app.state.context.engine.connect() as conn:
        return [
            row.name for row in conn.execute(select(assets.c.name).where(assets.c.kind == kind))
        ]


def test_mandatory_needs_the_asset_to_be_visible_in_that_scope(service):
    # 只在 data-intel 部门可见的资产，不能设成全公司或别的部门必选
    with service.app.state.context.engine.connect() as conn:
        row = conn.execute(
            select(assets.c.asset_id, assets.c.name).where(
                assets.c.scope == "department", assets.c.kind != "Application"
            )
        ).first()
    url = f"/api/v1/assets/{row.asset_id}/mandatory"
    assert service.post(url, json={"entry": "company"}).status_code == 422
    assert service.post(url, json={"entry": "department:market"}).status_code == 422
    assert service.post(url, json={"entry": "department:data-intel"}).status_code == 200


def test_personal_mandatory_only_in_private_chat(service):
    _clear(service)
    solution = _id(service, "bank-core-migration-solution")
    assert (
        service.post(
            f"/api/v1/assets/{solution}/mandatory", json={"entry": "user:chen"}, headers=_as("chen")
        ).status_code
        == 200
    )
    # 共享会话（平台不说是私聊）：不带个人必选
    shared = _bundle(service, agent_key="coder-default")
    assert "bank-core-migration-solution" not in _names(shared)
    # 私聊：带上
    private = _bundle(service, agent_key="coder-default", personal=True)
    assert "bank-core-migration-solution" in _names(private)
    assert private["revision"] != shared["revision"]
    # 别人的私聊不带 chen 的个人必选
    other = service.post(
        "/internal/v1/agent-bundle",
        json={"username": "wang", "engagement_slug": "policy-import", "personal": True},
        headers={"X-FDE-Service-Key": KEY},
    ).json()
    assert "bank-core-migration-solution" not in _names(other)


def test_changing_mandatory_invalidates_known_revision(service):
    first = _bundle(service, agent_key="coder-default")
    assert _bundle(service, agent_key="coder-default", known_revision=first["revision"]) == {
        "revision": first["revision"],
        "unchanged": True,
    }
    rule = _id(service, "10-engineering")
    service.post(f"/api/v1/assets/{rule}/mandatory/remove", json={"entry": "company"})
    changed = _bundle(service, agent_key="coder-default", known_revision=first["revision"])
    assert "files" in changed and "10-engineering" not in _names(changed)
    assert base64.b64decode(first["files"]["rules/10-engineering.md"])
