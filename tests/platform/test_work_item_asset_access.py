"""任务资产只信平台给出的归属；资产可见性不能替代任务访问权限。"""

import json

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select

from fde_asset.api.app import create_app
from fde_asset.core.db import asset_audiences, assets, work_item_assets
from fde_asset.modules.asset.indexer import index_all
from fde_asset.platform.identity import PlatformDirectory, PlatformUnavailable

KEY = "controlled-task-asset-service-key"
PATH = "/api/v1/work-items/w-1"
INTERNAL = "/internal/v1/work-items/w-1/sop-step"
CALLER = {"username": "chen", "engagement_slug": "policy-import"}
HEADERS = {"X-FDE-Service-Key": KEY}


@pytest.fixture()
def service(seeded):
    directory_path = seeded.root / "directory.json"
    directory = json.loads(directory_path.read_text())
    directory["work_items"] = {
        "w-1": {"engagement_slug": "policy-import"},
        "w-other": {"engagement_slug": "claims-recon"},
    }
    directory["users"]["viewer"] = {
        "memberships": [{"engagement_slug": "policy-import", "role": "viewer"}]
    }
    directory_path.write_text(json.dumps(directory))
    with TestClient(create_app(seeded.model_copy(update={"server_service_key": KEY}))) as client:
        ctx = client.app.state.context
        index_all(ctx.engine, ctx.repo_port, ctx.repos(), file_store=ctx.file_store)
        client.headers.update({"X-FDE-User": "chen"})
        yield client


def sop_id(service):
    with service.app.state.context.engine.connect() as conn:
        return conn.execute(
            select(assets.c.asset_id).where(assets.c.name == "delivery-standard")
        ).scalar_one()


def bind(service, **changes):
    return service.post(
        PATH + "/assets",
        json={"asset_id": sop_id(service), "role": "sop_step", "sop_step": 1, **changes},
    )


def stored(service):
    with service.app.state.context.engine.connect() as conn:
        return conn.execute(select(work_item_assets)).all()


def test_binding_uses_real_task_project_without_a_browser_slug(service):
    assert bind(service).status_code == 200
    assert stored(service)[0].engagement_slug == "policy-import"


def test_forged_project_is_rejected_before_creating_a_binding(service):
    assert bind(service, engagement_slug="claims-recon").status_code == 403
    assert stored(service) == []


@pytest.mark.parametrize(
    "suffix,method", [("/assets", "get"), ("/sop-step-summary", "get"), ("/sop-step", "delete")]
)
def test_outsider_cannot_read_or_delete_a_task_binding(service, suffix, method):
    assert bind(service).status_code == 200
    service.headers.update({"X-FDE-User": "zhao"})
    assert getattr(service, method)(PATH + suffix).status_code == 404
    assert len(stored(service)) == 1


def test_outsider_cannot_replace_a_binding_by_omitting_project(service):
    assert bind(service).status_code == 200
    service.headers.update({"X-FDE-User": "zhao"})
    assert bind(service, sop_step=2).status_code == 404
    assert stored(service)[0].sop_step == 1


def test_viewer_can_read_but_cannot_bind_delete_or_lock(service):
    assert bind(service).status_code == 200
    service.headers.update({"X-FDE-User": "viewer"})
    assert service.get(PATH + "/assets").status_code == 200
    assert service.get(PATH + "/sop-step-summary").json()["found"] is True
    assert bind(service, sop_step=2).status_code == 403
    assert service.delete(PATH + "/sop-step").status_code == 403
    assert service.get(PATH + "/sop-step-summary", params={"lock": True}).status_code == 403
    assert stored(service)[0].locked is False


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_unknown_local_task_is_not_authorized_even_for_admin(service, method):
    service.headers.update({"X-FDE-User": "admin"})
    path = "/api/v1/work-items/not-in-directory/" + ("sop-step" if method == "delete" else "assets")
    kwargs = (
        {"json": {"asset_id": sop_id(service), "role": "sop_step", "sop_step": 1}}
        if method == "post"
        else {}
    )
    assert getattr(service, method)(path, **kwargs).status_code == 404
    assert stored(service) == []


@pytest.mark.parametrize("step", [None, True, "1", 1.5, 0, 999])
def test_sop_binding_requires_an_existing_integer_step(service, step):
    assert bind(service, sop_step=step).status_code == 422
    assert stored(service) == []


def test_legacy_empty_project_binding_is_readable_and_repaired_on_update(service):
    ctx = service.app.state.context
    with ctx.engine.begin() as conn:
        conn.execute(
            work_item_assets.insert().values(
                work_item_id="w-1",
                engagement_slug="",
                asset_id=sop_id(service),
                role="sop_step",
                sop_step=1,
            )
        )
    assert service.get(PATH + "/assets").json()["items"][0]["sop_step"] == 1
    assert service.get(INTERNAL, params=CALLER, headers=HEADERS).json()["found"] is True
    assert bind(service, sop_step=2).status_code == 200
    assert stored(service)[0].engagement_slug == "policy-import"


def test_wrong_persisted_project_cannot_be_read_or_replaced(service):
    ctx = service.app.state.context
    with ctx.engine.begin() as conn:
        conn.execute(
            work_item_assets.insert().values(
                work_item_id="w-1",
                engagement_slug="claims-recon",
                asset_id=sop_id(service),
                role="sop_step",
                sop_step=1,
            )
        )
    assert service.get(PATH + "/assets").status_code == 404
    assert service.get(PATH + "/sop-step-summary").status_code == 404
    assert service.get(INTERNAL, params=CALLER, headers=HEADERS).status_code == 404
    assert bind(service).status_code == 404
    assert service.delete(PATH + "/sop-step").status_code == 404


def test_hidden_sop_is_not_disclosed_through_summary_list_or_agent(service):
    assert bind(service).status_code == 200
    ctx = service.app.state.context
    asset_id = sop_id(service)
    with ctx.engine.begin() as conn:
        conn.execute(asset_audiences.delete().where(asset_audiences.c.asset_id == asset_id))
        conn.execute(
            asset_audiences.insert().values(
                asset_id=asset_id, audience="engagement", code="claims-recon"
            )
        )
    assert service.get(PATH + "/assets").json()["items"] == []
    assert service.get(PATH + "/sop-step-summary").status_code == 404
    response = service.get(INTERNAL, params=CALLER, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"found": False, "work_item_id": "w-1"}


def test_agent_step_requires_caller_context_and_checks_real_project(service):
    assert bind(service).status_code == 200
    assert service.get(INTERNAL, headers=HEADERS).status_code == 422
    assert service.get(INTERNAL, params=CALLER).status_code == 404
    for caller in (
        {"username": "zhao", "engagement_slug": "policy-import"},
        {"username": "chen", "engagement_slug": "claims-recon"},
    ):
        assert service.get(INTERNAL, params=caller, headers=HEADERS).status_code == 404
    payload = service.get(INTERNAL, params=CALLER, headers=HEADERS).json()
    assert payload["found"] is True and payload["step"] == 1 and payload["prompt_md"]


def test_revoked_local_membership_stops_public_and_agent_reads(service):
    assert bind(service).status_code == 200
    source = service.app.state.context.directory
    directory = source._load()
    directory["users"]["chen"]["memberships"] = []
    source.write(directory)
    assert service.get(PATH + "/assets").status_code == 404
    assert service.get(INTERNAL, params=CALLER, headers=HEADERS).status_code == 404


def test_platform_task_authorization_is_live_and_uses_service_credentials(service):
    seen = []
    status = 200

    def platform(request):
        assert request.url.path == "/internal/identity/work-items/w-1/access"
        assert request.headers["X-FDE-Service-Key"] == KEY
        seen.append(dict(request.url.params))
        return httpx.Response(
            status, json={"work_item_id": "w-1", "engagement_slug": "policy-import"}
        )

    ctx = service.app.state.context
    principal = ctx.directory.resolve("chen")
    from fde_asset.api.deps import get_principal

    service.app.dependency_overrides[get_principal] = lambda: principal
    ctx.directory = PlatformDirectory(
        "http://platform",
        KEY,
        ttl_seconds=60,
        client=httpx.Client(transport=httpx.MockTransport(platform)),
    )
    assert bind(service).status_code == 200
    assert service.get(PATH + "/assets").status_code == 200
    status = 404
    assert service.get(PATH + "/assets").status_code == 404
    assert bind(service).status_code == 404
    status = 503
    assert service.delete(PATH + "/sop-step").status_code == 503
    assert seen == [
        {"username": "chen", "write": "true"},
        {"username": "chen", "write": "false"},
        {"username": "chen", "write": "false"},
        {"username": "chen", "write": "true"},
        {"username": "chen", "write": "true"},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"work_item_id": "wrong", "engagement_slug": "policy-import"},
        {"work_item_id": "w-1", "engagement_slug": ""},
    ],
)
def test_platform_access_rejects_malformed_trusted_responses(payload):
    directory = PlatformDirectory(
        "http://platform",
        KEY,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ),
    )
    with pytest.raises(PlatformUnavailable):
        directory.work_item_access("w-1", "chen", write=False)
