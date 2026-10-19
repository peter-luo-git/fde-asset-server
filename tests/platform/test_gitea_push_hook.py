"""资产仓库的 Gitea 推送回调（B9）：验签，只认资产仓库，受理后马上同步、重建索引。"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from fde_asset.api.app import create_app
from fde_asset.api.routes_internal import _push_state

KEY = "controlled-push-hook-service-key"


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_push_hook_verifies_signature_and_reindexes(seeded, monkeypatch):
    settings = seeded.model_copy(update={"server_service_key": KEY})
    secret = settings.gitea_webhook_secret()
    assert secret and secret != KEY, "密钥由服务密钥派生，不直接用服务密钥"
    calls = []
    import fde_asset.modules.asset.indexer as indexer

    original = indexer.index_all
    monkeypatch.setattr(indexer, "index_all", lambda *a, **k: calls.append(1) or original(*a, **k))
    with TestClient(create_app(settings)) as client:
        body = json.dumps({"ref": "refs/heads/main", "repository": {"name": "assets"}}).encode()
        url = "/internal/v1/gitea-push"
        assert (
            client.post(url, content=body, headers={"X-Gitea-Signature": "0" * 64}).status_code
            == 403
        )
        other = json.dumps(
            {"ref": "refs/heads/main", "repository": {"name": "policy-import"}}
        ).encode()
        ignored = client.post(
            url, content=other, headers={"X-Gitea-Signature": _sign(secret, other)}
        )
        assert ignored.status_code == 202 and ignored.json() == {"accepted": False}
        accepted = client.post(
            url, content=body, headers={"X-Gitea-Signature": _sign(secret, body)}
        )
        assert accepted.status_code == 202 and accepted.json()["accepted"] is True
        deadline = time.monotonic() + 30
        while _push_state["running"] and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _push_state["running"]
        assert calls, "库里还没有这个版本，要重建索引"


def test_push_hook_is_off_without_service_key(seeded):
    with TestClient(create_app(seeded.model_copy(update={"server_service_key": ""}))) as client:
        assert client.post("/internal/v1/gitea-push", content=b"{}").status_code == 404
