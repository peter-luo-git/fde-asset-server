"""Dedicated rerank HTTP protocol; synthetic transport, no service/model/DB."""

from contextlib import contextmanager
import json
import logging
import socket
from types import SimpleNamespace

import httpx
import pytest

from fde_asset.modules.asset import ask
from fde_asset.platform.identity import Principal
from fde_asset.platform.llm.client import LlmConfig
from fde_asset.platform.llm.reranker import HttpReranker, parse_results


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
