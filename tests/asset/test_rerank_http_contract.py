"""Dedicated rerank HTTP protocol; synthetic transport, no service/model/DB."""

from contextlib import contextmanager
import json
import logging
import socket
from types import SimpleNamespace

import httpx
import pytest

from fde_asset.modules.asset import ask
from fde_asset.platform.identity import LocalDirectory, Principal
from fde_asset.platform.llm.client import LlmConfig
from fde_asset.platform.llm.reranker import HttpReranker, build_reranker, parse_results


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": [{"message": {"content": "{}"}}]},
        {"results": None},
        {"results": {}},
        {"results": "invalid"},
        {"results": [{"no_index": 1}]},
        {"results": [{"index": 0}]},
        {"results": [{"index": True, "score": 0.8}]},
        {"results": [{"index": 0.5, "score": 0.8}]},
        {"results": [{"index": -1, "score": 0.8}]},
        {"results": [{"index": 0, "score": True}]},
        {"results": [{"index": 0, "score": float("nan")}]},
        {"results": [{"index": 0, "score": float("inf")}]},
        {"results": [{"index": 0, "score": 1.1}]},
        {"results": [{"index": 0, "score": 0.9}, {"index": 0, "score": 0.8}]},
    ],
)
def test_malformed_protocol_is_not_a_successful_empty_ranking(payload):
    with pytest.raises(ValueError, match="RERANK_RESPONSE_INVALID"):
        parse_results(payload)


@pytest.mark.parametrize(
    "field,index,score",
    [
        ("results", "index", "relevance_score"),
        ("data", "document_index", "score"),
    ],
)
def test_supported_formats_sort_and_keep_a_valid_empty_response(field, index, score):
    parsed = parse_results({field: [{index: 0, score: 0.1}, {index: 1, score: 0.9}]})
    assert [(row.index, row.score) for row in parsed] == [(1, 0.9), (0, 0.1)]
    assert parse_results({field: []}) == []


@pytest.fixture
def wire(monkeypatch):
    state = {"status": 200, "payload": {"results": []}, "calls": []}

    def handler(request):
        state["calls"].append((request.url.path, json.loads(request.content)))
        if state.get("timeout"):
            raise httpx.ReadTimeout("sensitive-url/body-must-not-log", request=request)
        return httpx.Response(state["status"], json=state["payload"])

    def forbidden(*_args, **_kwargs):
        raise AssertionError("real network forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        yield state


def ranker():
    return HttpReranker(LlmConfig("https://synthetic.invalid/v1", "synthetic-only", "ranker"))


@pytest.fixture
def catalog_rows(monkeypatch):
    row = SimpleNamespace(
        asset_id="visible",
        title="public-synthetic-document",
        summary="",
        conclusion_md="",
        tags_json="[]",
        name="visible",
        content_text="",
    )

    class Engine:
        @contextmanager
        def connect(self):
            yield self

        def execute(self, statement):
            # Execute boundary is synthetic; retain the production ACL SELECT.
            assert statement.whereclause is not None
            return SimpleNamespace(fetchall=lambda: [row])

    monkeypatch.setattr(ask.catalog, "reuse_counts", lambda _: {})
    monkeypatch.setattr(ask.catalog, "row_to_dict", lambda row, _: {"name": row.name})
    return Engine()


def test_chat_response_cannot_mark_ask_semantic(wire, catalog_rows):
    wire["payload"] = {"choices": [{"message": {"content": "sensitive-model-output"}}]}
    result = ask.ask(catalog_rows, Principal("synthetic-user"), "unrelated", reranker=ranker())
    assert result["mode"] == "keyword"
    assert wire["calls"] == [
        (
            "/v1/rerank",
            {
                "model": "ranker",
                "query": "unrelated",
                "documents": ["public-synthetic-document"],
                "top_n": 1,
            },
        )
    ]


@pytest.mark.parametrize("failure", ["http", "timeout", "schema"])
def test_fallback_logs_only_safe_exception_type_and_http_status(
    wire, catalog_rows, caplog, failure
):
    if failure == "http":
        wire.update(status=404, payload={"error": "sensitive-model-output"})
    elif failure == "timeout":
        wire["timeout"] = True
    else:
        wire["payload"] = {"choices": [{"message": {"content": "sensitive-model-output"}}]}
    caplog.set_level(logging.WARNING, logger=ask.__name__)
    result = ask.ask(
        catalog_rows, Principal("synthetic-user"), "sensitive-question", reranker=ranker()
    )
    assert result["mode"] == "keyword"
    records = [record for record in caplog.records if record.name == ask.__name__]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "exception_type=" in message
    assert ("http_status=404" if failure == "http" else "http_status=none") in message
    assert not records[0].exc_info
    for sensitive in (
        "synthetic.invalid",
        "synthetic-only",
        "sensitive-question",
        "sensitive-model-output",
        "public-synthetic-document",
        "sensitive-url",
    ):
        assert sensitive not in message


def test_real_rerank_threshold_does_not_change_semantic_mode(wire, catalog_rows):
    wire["payload"] = {"results": [{"index": 0, "relevance_score": 0.01}]}
    result = ask.ask(catalog_rows, Principal("synthetic-user"), "question", reranker=ranker())
    assert result["mode"] == "semantic" and result["items"] == []


def test_out_of_range_document_index_is_invalid(wire):
    wire["payload"] = {"results": [{"index": 5, "relevance_score": 0.8}]}
    with pytest.raises(ValueError, match="RERANK_RESPONSE_INVALID"):
        ranker().rank("question", ["one document"], 1)


@pytest.fixture
def configured_rerank_env(monkeypatch):
    monkeypatch.setenv("FDE_ASSET_RERANK_BASE_URL", "https://synthetic.invalid/v1")
    monkeypatch.setenv("FDE_ASSET_RERANK_API_KEY", "synthetic-only")
    monkeypatch.setenv("FDE_ASSET_RERANK_MODEL_NAME", "environment-ranker")


@pytest.mark.parametrize(
    "selected,expected", [("selected-ranker", "selected-ranker"), ("", "environment-ranker")]
)
def test_model_override_reaches_rerank_http_body(wire, configured_rerank_env, selected, expected):
    build_reranker(selected).rank("question", ["document"], 1)
    assert wire["calls"] == [
        (
            "/v1/rerank",
            {"model": expected, "query": "question", "documents": ["document"], "top_n": 1},
        )
    ]


@pytest.mark.parametrize("entry", ["ask", "mcp", "recommend"])
@pytest.mark.parametrize(
    "selected,expected", [("selected-ranker", "selected-ranker"), ("", "environment-ranker")]
)
def test_each_entry_uses_selected_rerank_model(
    wire, configured_rerank_env, monkeypatch, tmp_path, entry, selected, expected
):
    from fde_asset.api import routes_assets, routes_mcp, routes_recommend
    from fde_asset.modules.recommend.service import Target
    from fde_asset.platform import settings_store

    values = {
        "rerank_enabled": True,
        "rerank_model_name": selected,
        "rerank_model": "reason-only",
        "search_min_relevance": 5,
    }
    monkeypatch.setattr(settings_store, "get", lambda _engine, key: values[key])
    context = SimpleNamespace(
        engine=object(), directory=LocalDirectory(tmp_path / "directory.json")
    )
    principal = Principal("synthetic-user")

    def search(_engine, _principal, question, *, reranker, **_kwargs):
        reranker.rank(question, ["document"], 1)
        return {"mode": "semantic", "scanned": 1, "items": []}

    monkeypatch.setattr(ask, "ask", search)
    if entry == "ask":
        routes_assets.ask_assets("question", limit=1, context=context, principal=principal)
    elif entry == "mcp":
        routes_mcp._search(context, principal, {"question": "question"})
    else:
        monkeypatch.setattr(
            routes_recommend,
            "_target",
            lambda *_args: Target("agent", "synthetic", title="question"),
        )

        def compute(engine, principal, target, *, reranker, **kwargs):
            search(engine, principal, target.title, reranker=reranker)
            return [], "reranked"

        monkeypatch.setattr(routes_recommend.service, "compute", compute)
        routes_recommend.compute({"refresh": True}, context, principal)
    assert wire["calls"][0][1]["model"] == expected


def test_recommend_model_change_does_not_reuse_previous_model_cache(
    wire, configured_rerank_env, monkeypatch, tmp_path
):
    from fde_asset.api import routes_recommend
    from fde_asset.modules.recommend.service import Target
    from fde_asset.platform import settings_store
    from fde_asset.platform.cache import TtlCache

    values = {
        "rerank_enabled": True,
        "rerank_model_name": "first-ranker",
        "search_min_relevance": 5,
    }
    monkeypatch.setattr(settings_store, "get", lambda _engine, key: values[key])
    monkeypatch.setattr(routes_recommend, "recommendation_cache", TtlCache())
    monkeypatch.setattr(routes_recommend, "_target", lambda *_args: Target("agent", "synthetic"))

    def compute(_engine, _principal, _target, *, reranker, **_kwargs):
        reranker.rank("question", ["document"], 1)
        return [], "reranked"

    monkeypatch.setattr(routes_recommend.service, "compute", compute)
    context = SimpleNamespace(
        engine=object(), directory=LocalDirectory(tmp_path / "directory.json")
    )
    principal = Principal("synthetic-user")
    assert routes_recommend.compute({}, context, principal)["mode"] == "reranked"
    assert routes_recommend.compute({}, context, principal)["mode"] == "reranked-cached"
    values["rerank_model_name"] = "second-ranker"
    assert routes_recommend.compute({}, context, principal)["mode"] == "reranked"
    assert [body["model"] for _path, body in wire["calls"]] == ["first-ranker", "second-ranker"]


def test_rerank_selection_does_not_change_explanation_http_model(wire, monkeypatch):
    from fde_asset.api import routes_recommend
    from fde_asset.modules.recommend.service import Target
    from fde_asset.platform import settings_store

    monkeypatch.setenv("FDE_ASSET_LLM_BASE_URL", "https://synthetic.invalid/v1")
    monkeypatch.setenv("FDE_ASSET_LLM_API_KEY", "synthetic-only")
    monkeypatch.setenv("FDE_ASSET_REASON_MODEL", "environment-reason")
    values = {"rerank_model_name": "selected-ranker", "rerank_model": "selected-reason"}
    monkeypatch.setattr(settings_store, "get", lambda _engine, key: values[key])
    monkeypatch.setattr(routes_recommend, "_target", lambda *_args: Target("agent", "synthetic"))
    wire["payload"] = {"choices": [{"message": {"content": '{"asset": "reason"}'}}]}

    def explain(_engine, _principal, _target, _asset_ids, llm):
        return llm.complete_json("system", "question")

    monkeypatch.setattr(routes_recommend.service, "explain", explain)
    result = routes_recommend.explain(
        {"asset_ids": ["asset"], "refresh": True},
        SimpleNamespace(engine=object()),
        Principal("synthetic-user"),
    )
    assert result["reasons"] == {"asset": "reason"}
    assert wire["calls"][0][0] == "/v1/chat/completions"
    assert wire["calls"][0][1]["model"] == "selected-reason"


def test_model_override_alone_does_not_enable_unconfigured_rerank(monkeypatch):
    monkeypatch.delenv("FDE_ASSET_RERANK_BASE_URL", raising=False)
    monkeypatch.delenv("FDE_ASSET_RERANK_API_KEY", raising=False)
    assert build_reranker("selected-ranker").usable is False
