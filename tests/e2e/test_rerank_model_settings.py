"""Real HTTP + PostgreSQL model selection; only the external rerank service is synthetic."""

from __future__ import annotations

import json
import socket

import httpx
import pytest
from sqlalchemy import select

from fde_asset.core.db import create_engine_for, system_settings
from scripts.asset_demo import LiveServer


@pytest.fixture
def rerank_wire(monkeypatch):
    monkeypatch.setenv("FDE_ASSET_RERANK_BASE_URL", "https://synthetic.invalid/v1")
    monkeypatch.setenv("FDE_ASSET_RERANK_API_KEY", "synthetic-only")
    monkeypatch.setenv("FDE_ASSET_RERANK_MODEL_NAME", "environment-ranker")
    connect = socket.socket.connect

    def loopback_only(sock, address):
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            raise AssertionError("external network forbidden")
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    calls = []

    def handler(request):
        assert str(request.url) == "https://synthetic.invalid/v1/rerank"
        assert request.headers["Authorization"] == "Bearer synthetic-only"
        body = json.loads(request.content)
        calls.append(body)
        scores = [
            {"index": i, "relevance_score": 0.9 if "导入超过 1 万行" in text else 0.01}
            for i, text in enumerate(body["documents"])
        ]
        scores.sort(key=lambda item: item["relevance_score"], reverse=True)
        return httpx.Response(
            200,
            json={"results": scores[: body["top_n"]]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as wire:
        monkeypatch.setattr(httpx, "post", wire.post)
        yield calls


def search(client, entry, user):
    headers = {"X-FDE-User": user}
    if entry == "ask":
        response = client.get("/api/v1/assets/ask", params={"q": "导入很慢"}, headers=headers)
    elif entry == "recommend":
        response = client.post(
            "/api/v1/recommend/compute",
            headers=headers,
            json={"target_type": "engagement", "target_id": "policy-import", "refresh": True},
        )
    else:
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "search_assets",
                    "arguments": {"question": "导入很慢", "user": "admin"},
                },
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    if entry == "mcp":
        assert body["result"]["isError"] is False
        body = json.loads(body["result"]["content"][0]["text"])
    assert body["mode"] == ("reranked" if entry == "recommend" else "semantic")
    return body["items"]


@pytest.mark.parametrize("entry", ["ask", "recommend", "mcp"])
def test_persisted_model_selection_and_clear_keep_real_http_acl(seeded, rerank_wire, entry):
    server = LiveServer(seeded)
    engine = create_engine_for(seeded.database_target())
    try:
        with server as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            admin = {"X-FDE-User": "admin"}
            assert client.post("/api/v1/admin/assets/reindex", headers=admin).status_code == 200
            settings = "/api/v1/settings"
            initial = {"rerank_model_name": "selected-ranker", "rerank_model": "reason-only"}
            assert client.put(settings, headers=admin, json={"values": initial}).status_code == 200

            denied = client.put(
                settings,
                headers={"X-FDE-User": "chen"},
                json={"values": {"rerank_model_name": "forbidden-ranker"}},
            )
            assert denied.status_code == 403
            rejected = client.put(
                settings,
                headers=admin,
                json={"values": {"rerank_model": "not-committed", "rerank_model_name": "bad\n"}},
            )
            assert rejected.status_code == 422
            with engine.connect() as conn:
                persisted = dict(
                    conn.execute(select(system_settings.c.key, system_settings.c.value)).all()
                )
            assert {key: persisted[key] for key in initial} == initial

            for selected, expected in (
                ("selected-ranker", "selected-ranker"),
                ("", "environment-ranker"),
            ):
                saved = client.put(
                    settings, headers=admin, json={"values": {"rerank_model_name": selected}}
                )
                assert saved.status_code == 200
                assert saved.json()["values"]["rerank_model"] == "reason-only"
                before = len(rerank_wire)
                visible = search(client, entry, "chen")
                hidden = search(client, entry, "zhao")
                assert len(rerank_wire) == before + 2
                assert [call["model"] for call in rerank_wire[before:]] == [expected, expected]
                assert any(item["title"] == "导入超过 1 万行报错" for item in visible)
                assert all(item["title"] != "导入超过 1 万行报错" for item in hidden)
                assert any("导入超过 1 万行" in text for text in rerank_wire[-2]["documents"])
                assert all("导入超过 1 万行" not in text for text in rerank_wire[-1]["documents"])
    finally:
        engine.dispose()
    assert not server.thread.is_alive()
