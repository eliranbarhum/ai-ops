import logging
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from shared import CONFIG_STORE_URL, LLM_GATEWAY_URL
from workspace_executor import execute as workspace_execute
from routers.maintenance import is_in_maintenance_window

logger = logging.getLogger("api-gateway")
router = APIRouter()


class WorkspaceGenerateRequest(BaseModel):
    description: str


@router.post("/api/v1/workspace/generate")
async def workspace_generate(request: WorkspaceGenerateRequest):
    async with httpx.AsyncClient(timeout=660.0) as client:
        try:
            resp = await client.post(f"{LLM_GATEWAY_URL}/generate", json={"description": request.description})
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="LLM gateway timed out")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=e.response.text)
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"LLM gateway unreachable: {e}")


@router.post("/api/v1/workspace/execute")
async def workspace_exec(request: Request):
    if not await is_in_maintenance_window():
        raise HTTPException(423, detail="Workspace execution is blocked outside a maintenance window. Configure one in Settings → Maintenance.")
    body = await request.json()
    spec = body.get("spec", {})
    description = body.get("description", "")
    result = await workspace_execute(spec, CONFIG_STORE_URL)
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(f"{CONFIG_STORE_URL}/workspace",
                              json={"description": description, "spec": spec, "result": result})
        except Exception as e:
            logger.warning(f"Failed to persist workspace entry: {e}")
    return result


@router.get("/api/v1/workspace/llm-status")
async def workspace_llm_status():
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            cfg_resp = await client.get(f"{CONFIG_STORE_URL}/config/raw")
            cfg = cfg_resp.json()
        except Exception:
            return {"provider": "unknown", "status": "config_error", "detail": "Cannot reach config-store"}

    provider = cfg.get("llm_provider", "anthropic")

    if provider == "ollama":
        url = cfg.get("vllm_url", "http://vllm-server:11434").rstrip("/")
        model = cfg.get("vllm_model", "qwen2.5:14b")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{url}/api/tags")
            if r.status_code == 200:
                loaded = [m.get("name", "") for m in r.json().get("models", [])]
                ready = model in loaded
                return {
                    "provider": "ollama", "model": model,
                    "status": "ready" if ready else "model_not_loaded",
                    "loaded_models": loaded,
                    "detail": f"Model '{model}' loaded" if ready else f"Model '{model}' not yet pulled",
                    "slow_warning": True,
                }
            return {"provider": "ollama", "model": model, "status": "unreachable", "detail": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"provider": "ollama", "model": model, "status": "unreachable", "detail": str(e)}
    elif provider == "anthropic":
        return {"provider": "anthropic", "model": cfg.get("anthropic_model", ""),
                "status": "ready" if cfg.get("anthropic_api_key") else "no_key",
                "detail": "API key configured" if cfg.get("anthropic_api_key") else "No API key set"}
    elif provider == "openai":
        return {"provider": "openai", "model": cfg.get("openai_model", ""),
                "status": "ready" if cfg.get("openai_api_key") else "no_key",
                "detail": "API key configured" if cfg.get("openai_api_key") else "No API key set"}
    elif provider == "gemini":
        return {"provider": "gemini", "model": cfg.get("gemini_model", ""),
                "status": "ready" if cfg.get("gemini_api_key") else "no_key",
                "detail": "API key configured" if cfg.get("gemini_api_key") else "No API key set"}
    return {"provider": provider, "status": "unknown"}


@router.get("/api/v1/workspace/history")
async def workspace_history():
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{CONFIG_STORE_URL}/workspace")
        resp.raise_for_status()
        return resp.json()


@router.delete("/api/v1/workspace/history/{entry_id}")
async def workspace_delete(entry_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(f"{CONFIG_STORE_URL}/workspace/{entry_id}")
        resp.raise_for_status()
        return resp.json()
