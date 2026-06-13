import json as _json
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from shared import CONFIG_STORE_URL, LLM_GATEWAY_URL

router = APIRouter()


def _get_user(request: Request) -> str:
    return (
        request.headers.get("x-forwarded-preferred-username")
        or request.headers.get("x-forwarded-user")
        or request.headers.get("x-forwarded-email")
        or "anonymous"
    )


@router.post("/api/v1/agent/chat")
async def agent_chat(request: Request):
    body = await request.json()
    body.setdefault("user_id", _get_user(request))

    async def _proxy():
        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                async with client.stream(
                    "POST", f"{LLM_GATEWAY_URL}/agent/chat",
                    json=body, timeout=300.0,
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            yield f"{line}\n"
            except Exception as e:
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        _proxy(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/v1/agent/conversations")
async def agent_list_conversations(request: Request):
    user = _get_user(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{CONFIG_STORE_URL}/conversations", params={"user_id": user})
        resp.raise_for_status()
        return resp.json()


@router.get("/api/v1/agent/conversations/{conv_id}")
async def agent_get_conversation(conv_id: str, request: Request):
    user = _get_user(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{CONFIG_STORE_URL}/conversations/{conv_id}", params={"user_id": user})
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Conversation not found")
        resp.raise_for_status()
        return resp.json()


@router.post("/api/v1/agent/conversations")
async def agent_save_conversation(request: Request):
    body = await request.json()
    body.setdefault("user_id", _get_user(request))
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{CONFIG_STORE_URL}/conversations", json=body)
        resp.raise_for_status()
        return resp.json()


@router.delete("/api/v1/agent/conversations/{conv_id}")
async def agent_delete_conversation(conv_id: str, request: Request):
    user = _get_user(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(
            f"{CONFIG_STORE_URL}/conversations/{conv_id}", params={"user_id": user}
        )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Conversation not found")
        resp.raise_for_status()
        return resp.json()
