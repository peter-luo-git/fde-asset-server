"""骨架自检：应用能装配、健康检查可用。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fde_asset.api.app import create_app


def test_live_returns_ok(settings) -> None:
    client = TestClient(create_app(settings))
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_reports_checks(settings) -> None:
    client = TestClient(create_app(settings))
    payload = client.get("/health/ready").json()
    assert set(payload["checks"]) == {"data_dir", "repo_dir", "database"}
    assert payload["status"] == "ok"
