"""骨架自检：应用能装配、健康检查可用。"""

from fastapi.testclient import TestClient

from fde_asset.api.app import create_app


def test_live_returns_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_reports_checks() -> None:
    client = TestClient(create_app())
    payload = client.get("/health/ready").json()
    assert set(payload["checks"]) == {"data_dir", "repo_dir"}
