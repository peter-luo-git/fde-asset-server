"""给 Agent 的检索工具：按 MCP 协议一问一答，看得到什么由请求的身份决定。"""

from __future__ import annotations

import json

import pytest

from fde_asset.api import routes_mcp
from fde_asset.platform.llm.reranker import NullReranker, Scored
from tests.conftest import as_user


@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch) -> None:
    """同一轮里别的用例可能把 .env.local 的模型配置加载进了环境变量；这里一律当作没配模型。"""
    monkeypatch.setattr(routes_mcp.reranker_module, "build_reranker", NullReranker)


def rpc(client, method: str, params: dict | None = None, request_id: int = 1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body)


def call(client, name: str, arguments: dict) -> tuple[bool, object]:
    result = rpc(client, "tools/call", {"name": name, "arguments": arguments}).json()["result"]
    text = result["content"][0]["text"]
    return result["isError"], (text if result["isError"] else json.loads(text))


def test_handshake_and_tool_list(client) -> None:
    chen = as_user(client, "chen")
    init = rpc(chen, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}}).json()
    assert init["id"] == 1
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert init["result"]["serverInfo"]["name"] == "fde-asset"
    assert "tools" in init["result"]["capabilities"]
    assert "search_assets" in init["result"]["instructions"]

    # 握手后的通知没有 id，不需要回应
    done = chen.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert done.status_code == 202 and done.content == b""

    tools = rpc(chen, "tools/list").json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["search_assets", "get_asset"]
    assert tools[0]["inputSchema"]["required"] == ["question"]
    assert "Application" in tools[0]["inputSchema"]["properties"]["kind"]["enum"]
    assert rpc(chen, "ping").json()["result"] == {}


def test_search_then_read(client) -> None:
    chen = as_user(client, "chen")
    is_error, found = call(chen, "search_assets", {"question": "导入超过 1 万行报错怎么办"})
    assert not is_error
    assert found["mode"] == "keyword" and found["scanned"] > 0
    top = found["items"][0]
    assert top["title"] == "导入超过 1 万行报错"
    assert set(top) == {"asset_id", "ref", "kind", "title", "summary", "scope", "relevance"}

    is_error, asset = call(chen, "get_asset", {"asset_id": top["asset_id"]})
    assert not is_error
    assert asset["title"] == "导入超过 1 万行报错"
    assert "分批提交" in asset["content"], "要拿得到正文，不只是摘要"
    assert asset["truncated"] is False

    # 引用写法也认，带不带方括号都行
    for ref in (top["ref"], top["ref"].strip("[]")):
        is_error, by_ref = call(chen, "get_asset", {"ref": ref})
        assert not is_error and by_ref["asset_id"] == top["asset_id"]


def test_search_uses_the_semantic_model_when_configured(client, monkeypatch) -> None:
    class Fake:
        usable = True

        def rank(self, query, documents, top_n):
            hits = [i for i, text in enumerate(documents) if "序列跳号" in text]
            return [Scored(index=i, score=0.8) for i in hits][:top_n]

    monkeypatch.setattr(routes_mcp.reranker_module, "build_reranker", lambda: Fake())
    is_error, found = call(
        as_user(client, "chen"), "search_assets", {"question": "换库之后编号不连续"}
    )
    assert not is_error and found["mode"] == "semantic"
    assert [item["title"] for item in found["items"]] == ["Oracle 迁移 PostgreSQL 后序列跳号"]


def test_agent_only_sees_what_its_user_may_see(client) -> None:
    chen = as_user(client, "chen")
    _, mine = call(chen, "search_assets", {"question": "导入超过 1 万行报错怎么办"})
    project_asset = mine["items"][0]
    assert project_asset["scope"] == "engagement"

    zhao = as_user(client, "zhao")
    _, theirs = call(zhao, "search_assets", {"question": "导入超过 1 万行报错怎么办"})
    assert all(item["scope"] == "company" for item in theirs["items"])
    assert project_asset["asset_id"] not in {item["asset_id"] for item in theirs["items"]}

    # 就算知道 asset_id 或引用也读不到，而且和「不存在」是同一种回答
    for arguments in ({"asset_id": project_asset["asset_id"]}, {"ref": project_asset["ref"]}):
        is_error, message = call(zhao, "get_asset", arguments)
        assert is_error and message == "没有这份资产，或者当前身份无权查看"
    is_error, message = call(zhao, "get_asset", {"asset_id": "no-such-asset"})
    assert is_error and message == "没有这份资产，或者当前身份无权查看"


def test_no_identity_no_tools(client) -> None:
    client.headers.pop("X-FDE-User", None)
    assert rpc(client, "tools/list").status_code == 401
    # 工具参数里自称是谁没有用：身份只认请求本身
    response = rpc(
        client,
        "tools/call",
        {"name": "search_assets", "arguments": {"question": "x", "user": "admin"}},
    )
    assert response.status_code == 401


def test_bad_input_is_reported_to_the_model_not_crashed(client) -> None:
    chen = as_user(client, "chen")
    for name, arguments, expected in (
        ("search_assets", {"question": "  "}, "question 不能为空"),
        ("search_assets", {"question": "x", "kind": "Nope"}, "没有这种资产类型"),
        ("search_assets", {"question": "x", "limit": "很多"}, "limit 要是 1 到 10 的整数"),
        ("get_asset", {}, "asset_id 和 ref 至少给一个"),
        ("get_asset", {"ref": "不是引用"}, "ref 的写法不对"),
    ):
        is_error, message = call(chen, name, arguments)
        assert is_error and expected in message, (name, arguments, message)

    assert rpc(chen, "tools/call", {"name": "delete_everything"}).json()["error"]["code"] == -32602
    assert rpc(chen, "resources/list").json()["error"]["code"] == -32601
    assert chen.post("/mcp", json={"method": "ping"}).json()["error"]["code"] == -32600
    assert chen.get("/mcp").status_code == 405


def test_limit_is_capped_and_batches_work(client) -> None:
    chen = as_user(client, "chen")
    _, found = call(chen, "search_assets", {"question": "保单 导入 迁移 规范 流程", "limit": 99})
    assert len(found["items"]) <= routes_mcp.SEARCH_LIMIT_MAX

    batch = chen.post(
        "/mcp",
        json=[
            {"jsonrpc": "2.0", "id": "a", "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": "b", "method": "tools/list"},
        ],
    ).json()
    assert [reply["id"] for reply in batch] == ["a", "b"]
