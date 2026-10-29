"""Model setting routes and validation; synthetic SQL boundary, no database/service."""

from contextlib import contextmanager
import json
import socket
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from fde_asset.api import routes_assets
from fde_asset.platform import settings_store
from fde_asset.platform.identity import LocalDirectory, Principal


@pytest.fixture
def setting_context(monkeypatch):
    class Engine:
        def __init__(self):
            self.rows = {}

        @contextmanager
        def begin(self):
            before = self.rows.copy()
            try:
                yield self
            except Exception:
                self.rows = before
                raise

        @contextmanager
        def connect(self):
            yield self

        def execute(self, _statement):
            return [SimpleNamespace(**row) for row in self.rows.values()]

    def upsert(conn, _table, values, _keys):
        conn.rows[values["key"]] = values

    monkeypatch.setattr(settings_store, "upsert", upsert)
    return SimpleNamespace(engine=Engine())


def test_rerank_setting_roundtrip_keeps_reason_model_separate(setting_context):
    context, admin = setting_context, Principal("admin", is_admin=True)
    defaults = {item["key"]: item for item in routes_assets.read_settings(context, admin)["items"]}
    assert defaults["rerank_model_name"]["value"] == ""
    selected = "ranker/" + "x" * 248
    result = routes_assets.write_settings(
        {"values": {"rerank_model_name": selected, "rerank_model": "reason-model"}}, context, admin
    )
    assert result["values"]["rerank_model_name"] == selected
    reread = {
        item["key"]: item["value"] for item in routes_assets.read_settings(context, admin)["items"]
    }
    assert reread["rerank_model_name"] == selected
    assert reread["rerank_model"] == "reason-model"
    assert context.engine.rows["rerank_model_name"]["updated_by"] == "admin"
    cleared = routes_assets.write_settings({"values": {"rerank_model_name": ""}}, context, admin)
    assert cleared["values"]["rerank_model_name"] == ""
    assert cleared["values"]["rerank_model"] == "reason-model"


@pytest.mark.parametrize(
    "value",
    [
        None,
        123,
        True,
        [],
        {},
        "x" * 256,
        "line\nfeed",
        "tab\tmodel",
        "null\x00model",
        "del\x7fmodel",
        "next\x85model",
    ],
)
def test_rerank_setting_rejects_invalid_values_without_persisting(setting_context, value):
    with pytest.raises(HTTPException) as caught:
        routes_assets.write_settings(
            {"values": {"rerank_model_name": value}},
            setting_context,
            Principal("admin", is_admin=True),
        )
    assert caught.value.status_code == 422
    assert setting_context.engine.rows == {}


def test_rerank_setting_requires_admin_and_rejects_connection_settings(setting_context):
    with pytest.raises(HTTPException) as denied:
        routes_assets.write_settings(
            {"values": {"rerank_model_name": "ranker"}}, setting_context, Principal("member")
        )
    assert denied.value.status_code == 403
    for key in ("rerank_base_url", "rerank_api_key"):
        with pytest.raises(HTTPException) as invalid:
            routes_assets.write_settings(
                {"values": {key: "synthetic"}}, setting_context, Principal("admin", is_admin=True)
            )
        assert invalid.value.status_code == 422
    assert setting_context.engine.rows == {}


@pytest.fixture
def settings_http(setting_context, monkeypatch, tmp_path):
    from fde_asset.api import app

    directory_file = tmp_path / "directory.json"
    directory_file.write_text(
        json.dumps({"users": {"admin": {"is_admin": True}, "member": {}}}), encoding="utf-8"
    )
    setting_context.settings = SimpleNamespace(identity_mode="dev")
    setting_context.directory = LocalDirectory(directory_file)
    monkeypatch.setattr(app, "build_context", lambda _: setting_context)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("real network forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    with TestClient(app.create_app()) as client:
        client.headers["X-FDE-User"] = "admin"
        yield client


@pytest.mark.parametrize(
    "changes",
    [
        {"rerank_model": "new-reason", "rerank_model_name": "invalid\nmodel"},
        {"rerank_model_name": "new-ranker", "unknown_setting": "invalid"},
        {"rerank_model_name": "new-ranker", "search_min_relevance": 0},
    ],
)
def test_invalid_batch_rolls_back_earlier_model_changes(settings_http, setting_context, changes):
    initial = {"rerank_model_name": "saved-ranker", "rerank_model": "saved-reason"}
    assert settings_http.put("/api/v1/settings", json={"values": initial}).status_code == 200
    before = setting_context.engine.rows.copy()

    response = settings_http.put("/api/v1/settings", json={"values": changes})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    reread = {
        item["key"]: item["value"] for item in settings_http.get("/api/v1/settings").json()["items"]
    }
    assert {key: reread[key] for key in initial} == initial
    assert setting_context.engine.rows == before


@pytest.mark.parametrize("user,status", [(None, 401), ("unknown", 401), ("member", 403)])
def test_model_update_http_authentication_keeps_saved_values(
    settings_http, setting_context, user, status
):
    assert (
        settings_http.put(
            "/api/v1/settings", json={"values": {"rerank_model_name": "saved-ranker"}}
        ).status_code
        == 200
    )
    before = setting_context.engine.rows.copy()
    if user is None:
        del settings_http.headers["X-FDE-User"]
    else:
        settings_http.headers["X-FDE-User"] = user

    response = settings_http.put(
        "/api/v1/settings", json={"values": {"rerank_model_name": "unauthorized-ranker"}}
    )

    assert response.status_code == status
    assert setting_context.engine.rows == before


def test_saving_and_clearing_model_refreshes_recommendation_http_model(settings_http, monkeypatch):
    from fde_asset.api import routes_recommend
    from fde_asset.modules.recommend.service import Target
    from fde_asset.platform.cache import TtlCache

    monkeypatch.setenv("FDE_ASSET_RERANK_BASE_URL", "https://synthetic.invalid/v1")
    monkeypatch.setenv("FDE_ASSET_RERANK_API_KEY", "synthetic-only")
    monkeypatch.setenv("FDE_ASSET_RERANK_MODEL_NAME", "environment-ranker")
    monkeypatch.setattr(routes_recommend, "recommendation_cache", TtlCache())
    monkeypatch.setattr(routes_recommend, "_target", lambda *_args: Target("agent", "synthetic"))

    def compute(_engine, _principal, _target, *, reranker, **_kwargs):
        reranker.rank("question", ["document"], 1)
        return [], "reranked"

    monkeypatch.setattr(routes_recommend.service, "compute", compute)
    requests = []

    def handler(request):
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"results": []})

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as wire:
        monkeypatch.setattr(httpx, "post", wire.post)
        for selected in ("saved-ranker", "", "replacement-ranker"):
            saved = settings_http.put(
                "/api/v1/settings", json={"values": {"rerank_model_name": selected}}
            )
            assert saved.status_code == 200
            assert saved.json()["values"]["rerank_model_name"] == selected
            result = settings_http.post("/api/v1/recommend/compute", json={})
            assert result.status_code == 200
            assert result.json()["mode"] == "reranked"
            cached = settings_http.post("/api/v1/recommend/compute", json={})
            assert cached.json()["mode"] == "reranked-cached"

    assert requests == [
        (
            "/v1/rerank",
            {"model": model, "query": "question", "documents": ["document"], "top_n": 1},
        )
        for model in ("saved-ranker", "environment-ranker", "replacement-ranker")
    ]
