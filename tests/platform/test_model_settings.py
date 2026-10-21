"""Model setting routes and validation; synthetic SQL boundary, no database/service."""

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from fde_asset.api import routes_assets
from fde_asset.platform import settings_store
from fde_asset.platform.identity import Principal


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
