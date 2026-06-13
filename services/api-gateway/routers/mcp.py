import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from shared import LLM_GATEWAY_URL

router = APIRouter()


@router.post("/mcp")
async def mcp_proxy(request: Request):
    body = await request.body()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{LLM_GATEWAY_URL}/mcp",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        return resp.json()


@router.get("/mcp/sse")
async def mcp_sse_proxy():
    async def _stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{LLM_GATEWAY_URL}/mcp/sse") as resp:
                async for line in resp.aiter_lines():
                    if line:
                        yield f"{line}\n"
                    else:
                        yield "\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/mcp/messages")
async def mcp_messages_proxy(request: Request, session_id: str):
    body = await request.body()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{LLM_GATEWAY_URL}/mcp/messages",
            params={"session_id": session_id},
            content=body,
            headers={"Content-Type": "application/json"},
        )
        return resp.json()
