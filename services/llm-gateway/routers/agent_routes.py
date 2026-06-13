import json
import logging
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from providers import _get_cfg
from agent import run_agent_stream

logger = logging.getLogger("llm-gateway")
router = APIRouter()


class AgentChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    provider: str = ""


def _sse_agent(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/agent/chat")
async def agent_chat(request: AgentChatRequest):
    cfg = await _get_cfg()
    provider = request.provider or cfg.get("agent_llm_provider") or cfg.get("llm_provider", "anthropic")

    async def _stream():
        async for event in run_agent_stream(provider, cfg, request.history, request.message):
            yield _sse_agent(event)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
