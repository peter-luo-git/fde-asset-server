"""开发模式的身份列表：给页面上的身份切换用，正式环境不存在。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fde_asset.api.app import create_app


def test_lists_everyone_with_their_roles(client) -> None:
    # 这个接口就是用来选身份的，所以不带身份头也要能调
    client.headers.pop("X-FDE-User", None)
    response = client.get("/api/v1/dev/identities")
    assert response.status_code == 200, response.text
    by_id = {item["user_id"]: item for item in response.json()["items"]}

    assert set(by_id) >= {"chen", "li", "wang", "zhao", "admin"}
    assert by_id["li"]["display_name"] == "小李"
    assert by_id["li"]["roles"] == ["资产评审员"]
    assert "平台管理员" in by_id["admin"]["roles"]
    assert "policy-import 负责人" in by_id["wang"]["roles"]
    assert by_id["chen"]["roles"] == [] and by_id["chen"]["engagements"] == ["policy-import"]
    assert by_id["zhao"]["department_code"] == "market"


def test_not_available_outside_dev_mode(seeded) -> None:
    app = create_app(seeded.model_copy(update={"identity_mode": "oidc"}))
    with TestClient(app) as oidc:
        assert oidc.get("/api/v1/dev/identities").status_code == 404
