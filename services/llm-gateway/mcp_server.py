"""
MCP (Model Context Protocol) server exposing all VCF tools.

Implements two transports:
  - Streamable HTTP:  POST /mcp
  - SSE transport:    GET  /mcp/sse  (returns SSE stream)
                      POST /mcp/messages?session_id=...

Wire protocol: JSON-RPC 2.0 over MCP 2024-11-05.
"""

import json
import asyncio
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse

from agent import TOOL_SPECS, execute_tool

logger = logging.getLogger("llm-gateway.mcp")

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "mco-vcf-tools", "version": "1.0.0"}

# Active SSE sessions: session_id -> asyncio.Queue
_sessions: dict[str, asyncio.Queue] = {}


def _make_tool_list() -> list[dict]:
    tools = []
    for spec in TOOL_SPECS:
        props = spec.get("properties", {})
        required = [k for k, v in props.items() if v.get("required", False)]
        tools.append({
            "name": spec["name"],
            "description": spec["description"],
            "inputSchema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        })
    return tools


async def _dispatch(method: str, params: dict, req_id: Any, cfg: dict) -> dict:
    if method == "initialize":
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }

    if method == "notifications/initialized":
        return None  # no response needed

    if method == "tools/list":
        return {"tools": _make_tool_list()}

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            result_text, ok = await execute_tool(tool_name, arguments, cfg)
            return {
                "content": [{"type": "text", "text": result_text}],
                "isError": not ok,
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"Tool execution failed: {exc}"}],
                "isError": True,
            }

    raise ValueError(f"Unknown method: {method}")


def _jsonrpc_response(req_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def handle_streamable_http(request: Request, cfg: dict) -> StreamingResponse:
    """
    POST /mcp — Streamable HTTP transport.
    Handles batched JSON-RPC requests and responds as a single JSON object or
    newline-delimited JSON stream.
    """
    body = await request.json()

    # Normalise to list so we handle both single and batch
    requests_list = body if isinstance(body, list) else [body]
    responses = []

    for rpc in requests_list:
        req_id = rpc.get("id")
        method = rpc.get("method", "")
        params = rpc.get("params") or {}

        # Notifications have no id — send no response
        if req_id is None and method.startswith("notifications/"):
            continue

        try:
            result = await _dispatch(method, params, req_id, cfg)
            if result is None:
                continue
            responses.append(_jsonrpc_response(req_id, result))
        except ValueError as exc:
            responses.append(_jsonrpc_error(req_id, -32601, str(exc)))
        except Exception as exc:
            logger.exception("MCP dispatch error")
            responses.append(_jsonrpc_error(req_id, -32603, str(exc)))

    if not responses:
        # All notifications — return 204
        from fastapi.responses import Response
        return Response(status_code=204)

    payload = responses[0] if len(responses) == 1 else responses
    return StreamingResponse(
        iter([json.dumps(payload)]),
        media_type="application/json",
    )


async def handle_sse_init(cfg: dict) -> StreamingResponse:
    """
    GET /mcp/sse — SSE transport initialisation.
    Returns an SSE stream. First event is 'endpoint' with the session POST URL.
    Subsequent events are JSON-RPC responses pushed to the session queue.
    """
    session_id = str(uuid.uuid4())
    q: asyncio.Queue = asyncio.Queue()
    _sessions[session_id] = q

    async def _gen():
        # Send endpoint event so client knows where to POST messages
        yield f"event: endpoint\ndata: /mcp/messages?session_id={session_id}\n\n"
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30.0)
                    if msg is None:
                        break
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sessions.pop(session_id, None)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def handle_sse_message(request: Request, session_id: str, cfg: dict):
    """
    POST /mcp/messages?session_id=... — SSE transport message handler.
    Client POSTs JSON-RPC here; response is pushed to the SSE queue.
    """
    q = _sessions.get(session_id)
    if q is None:
        raise HTTPException(status_code=400, detail="Unknown session_id")

    body = await request.json()
    requests_list = body if isinstance(body, list) else [body]

    for rpc in requests_list:
        req_id = rpc.get("id")
        method = rpc.get("method", "")
        params = rpc.get("params") or {}

        if req_id is None and method.startswith("notifications/"):
            continue

        try:
            result = await _dispatch(method, params, req_id, cfg)
            if result is None:
                continue
            await q.put(_jsonrpc_response(req_id, result))
        except ValueError as exc:
            await q.put(_jsonrpc_error(req_id, -32601, str(exc)))
        except Exception as exc:
            logger.exception("MCP SSE dispatch error")
            await q.put(_jsonrpc_error(req_id, -32603, str(exc)))

    return {"accepted": True}
