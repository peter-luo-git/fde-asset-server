"""给 Agent 的检索工具：按 MCP（Model Context Protocol）的 HTTP 传输暴露两个工具。

Agent 以前只能被动接收平台塞进提示词的资产快照。有了这两个工具，它干活时遇到问题
可以自己查：`search_assets` 按问题找，`get_asset` 读全文。Agent 侧不用写调用代码——
创建会话时把 `POST /mcp` 这个地址挂进去，模型在工具列表里看到它们，自己决定何时调用。

只实现了工具调用需要的那一小部分协议（initialize / tools/list / tools/call / ping），
每次请求一问一答，不开 SSE 流，所以没有引入 MCP SDK。

身份与页面请求走同一套：工具不接受「我是谁」这种参数，看得到什么完全由请求的身份决定，
否则 Agent 可以自称任何人去读无权看的资产。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, Response
from fastapi.responses import JSONResponse

from fde_asset import __version__
from fde_asset.api.deps import ServiceContext, get_context, get_principal
from fde_asset.modules.asset import ask, catalog
from fde_asset.modules.asset.manifest import KIND_RULES
from fde_asset.platform import settings_store
from fde_asset.platform.identity import Principal
from fde_asset.platform.llm import reranker as reranker_module
from fde_asset.platform.refs.wiki import REF_PATTERN

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSION = "2025-03-26"
#: 全文一次给多少字符；再长模型也读不完，还挤占它的上下文
CONTENT_LIMIT = 20_000
SEARCH_LIMIT_MAX = 10

INSTRUCTIONS = (
    "这是团队的资产库：规范、标准作业程序、技能、方案、实施记录、问题、经验。"
    "遇到报错、拿不准的做法、或者要开始一类没做过的活，先用 search_assets 按问题找；"
    "找到相关的再用 get_asset 读全文。引用资产时写它的 ref（形如 [[case/xxx]]）。"
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_assets",
        "description": (
            "按问题找资产。直接用一句话描述遇到的问题或要做的事，按语义相关度返回，"
            "不要求和资产里的字眼一样。返回标题、一句话结论和 asset_id，要看全文再调 get_asset。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "一句话描述问题，例如：换库之后编号不连续",
                },
                "kind": {
                    "type": "string",
                    "enum": sorted(KIND_RULES),
                    "description": "只在某一类资产里找；不确定就不填",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": SEARCH_LIMIT_MAX,
                    "default": 5,
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "get_asset",
        "description": "读一份资产的全文：结论、适用边界和正文。用 search_assets 返回的 asset_id，或者 [[kind/name]] 形式的引用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "search_assets 返回的 asset_id"},
                "ref": {"type": "string", "description": "引用写法，例如 [[case/import-timeout]]"},
            },
        },
    },
]


class ToolError(Exception):
    """工具自己的出错（参数不对、找不到）：按协议作为工具结果返回，让模型看到并改正。"""


def _search(context: ServiceContext, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question") or "").strip()
    if not question:
        raise ToolError("question 不能为空")
    kind = args.get("kind") or None
    if kind is not None and kind not in KIND_RULES:
        raise ToolError(f"没有这种资产类型：{kind}；可选 {'、'.join(sorted(KIND_RULES))}")
    try:
        limit = max(1, min(int(args.get("limit") or 5), SEARCH_LIMIT_MAX))
    except (TypeError, ValueError):
        raise ToolError("limit 要是 1 到 10 的整数") from None
    result = ask.ask(
        context.engine,
        principal,
        question,
        reranker=reranker_module.build_reranker(),
        kind=kind,
        limit=limit,
        min_score=int(settings_store.get(context.engine, "search_min_relevance")) / 100,
    )
    return {
        "mode": result["mode"],
        "scanned": result["scanned"],
        "items": [
            {
                "asset_id": item["asset_id"],
                "ref": item["ref"],
                "kind": item["kind"],
                "title": item["title"],
                "summary": item["summary"],
                "scope": item["scope"],
                "relevance": item["relevance"],
            }
            for item in result["items"]
        ],
    }


def _get(context: ServiceContext, principal: Principal, args: dict[str, Any]) -> dict[str, Any]:
    asset_id = str(args.get("asset_id") or "").strip()
    ref = str(args.get("ref") or "").strip()
    if not asset_id and ref:
        text = ref if ref.startswith("[[") else f"[[{ref}]]"
        match = REF_PATTERN.fullmatch(text)
        if match is None:
            raise ToolError("ref 的写法不对，应该形如 [[case/import-timeout]]")
        resolved = catalog.resolve_ref(context.engine, principal, match.group(1), match.group(2))
        asset_id = resolved.get("asset_id", "")
    if not asset_id and not ref:
        raise ToolError("asset_id 和 ref 至少给一个")
    asset = catalog.get_asset(context.engine, principal, asset_id) if asset_id else None
    if asset is None:
        # 不存在和无权看是同一种回答，免得被用来探测资产是否存在
        raise ToolError("没有这份资产，或者当前身份无权查看")
    content = asset.get("content_text") or ""
    return {
        "asset_id": asset["asset_id"],
        "ref": asset["ref"],
        "kind": asset["kind"],
        "title": asset["title"],
        "summary": asset["summary"],
        "applicability": asset["applicability"],
        "lifecycle": asset["lifecycle"],
        "version": asset["version"],
        "content": content[:CONTENT_LIMIT],
        "truncated": len(content) > CONTENT_LIMIT,
    }


HANDLERS = {"search_assets": _search, "get_asset": _get}


def _text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _handle(message: Any, context: ServiceContext, principal: Principal) -> dict[str, Any] | None:
    """处理一条 JSON-RPC 消息；通知（没有 id）不需要回应，返回 None。"""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "不是合法的 JSON-RPC 2.0 请求")
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if "id" not in message:
        return None

    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fde-asset", "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        handler = HANDLERS.get(params.get("name"))
        if handler is None:
            return _error(request_id, -32602, f"没有这个工具：{params.get('name')}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "arguments 要是一个对象")
        try:
            result = _text_result(handler(context, principal, arguments))
        except ToolError as exc:
            result = _text_result(str(exc), is_error=True)
    else:
        return _error(request_id, -32601, f"不支持的方法：{method}")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@router.post("/mcp")
def mcp_endpoint(
    payload: Any = Body(...),
    context: ServiceContext = Depends(get_context),
    principal: Principal = Depends(get_principal),
) -> Response:
    """MCP 的 HTTP 入口：一条或一批 JSON-RPC 消息进来，同步返回 JSON。"""
    if isinstance(payload, list):
        replies = [reply for item in payload if (reply := _handle(item, context, principal))]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = _handle(payload, context, principal)
    return JSONResponse(reply) if reply is not None else Response(status_code=202)


@router.get("/mcp")
def mcp_stream_not_supported() -> Response:
    """协议允许服务端不提供 SSE 流，用 405 表明。"""
    return Response(status_code=405, headers={"Allow": "POST"})
