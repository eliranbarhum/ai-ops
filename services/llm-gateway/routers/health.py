from fastapi import APIRouter
from providers import _get_cfg

router = APIRouter()


@router.get("/health")
async def health():
    cfg = await _get_cfg()
    provider = cfg.get("llm_provider", "anthropic")
    return {"status": "healthy", "service": "llm-gateway", "provider": provider}
