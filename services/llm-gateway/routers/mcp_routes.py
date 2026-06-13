from fastapi import APIRouter, Request
from providers import _get_cfg
from mcp_server import handle_streamable_http, handle_sse_init, handle_sse_message

router = APIRouter()


@router.post("/mcp")
async def mcp_streamable(request: Request):
    cfg = await _get_cfg()
    return await handle_streamable_http(request, cfg)


@router.get("/mcp/sse")
async def mcp_sse_init():
    cfg = await _get_cfg()
    return await handle_sse_init(cfg)


@router.post("/mcp/messages")
async def mcp_sse_message(request: Request, session_id: str):
    cfg = await _get_cfg()
    return await handle_sse_message(request, session_id, cfg)
