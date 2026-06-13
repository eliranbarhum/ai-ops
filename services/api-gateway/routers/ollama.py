import logging
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from shared import CONFIG_STORE_URL
from vllm_deployer import deploy_vllm, get_vllm_status, delete_vllm, pull_model, get_pull_logs, delete_model, reset_pull_state

logger = logging.getLogger("api-gateway")
router = APIRouter()


class OllamaDeployRequest(BaseModel):
    model: str = "qwen2.5:14b"
    ram_gb: int = 32


class OllamaPullRequest(BaseModel):
    model: str = "smollm2:1.7b"
    ram_gb: int = 16


@router.get("/api/v1/ollama/models")
async def ollama_models():
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            cfg_resp = await client.get(f"{CONFIG_STORE_URL}/config/raw")
            cfg = cfg_resp.json()
            url = cfg.get("vllm_url", "http://vllm-server:11434").rstrip("/")
            r = await client.get(f"{url}/api/tags")
            if r.status_code == 200:
                models = [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
                return {"models": models, "url": url}
            return {"models": [], "url": url, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"models": [], "error": str(e)}


@router.post("/api/v1/ollama/deploy")
async def ollama_deploy(request: OllamaDeployRequest):
    logger.info(f"Ollama deploy: model={request.model} ram={request.ram_gb}GB")
    return await deploy_vllm(request.model, request.ram_gb)


@router.post("/api/v1/ollama/pull")
async def ollama_pull(request: OllamaPullRequest):
    logger.info(f"Ollama pull: model={request.model} ram={request.ram_gb}GB")
    return await pull_model(request.model, request.ram_gb)


@router.get("/api/v1/ollama/pull/logs")
async def ollama_pull_logs():
    return get_pull_logs()


@router.delete("/api/v1/ollama/model")
async def ollama_delete_model(request: Request):
    body = await request.json()
    model = body.get("model", "")
    if not model:
        raise HTTPException(status_code=400, detail="model field required")
    logger.info(f"Ollama delete model: {model}")
    return await delete_model(model)


@router.get("/api/v1/ollama/status")
async def ollama_status():
    return await get_vllm_status()


@router.post("/api/v1/ollama/pull/reset")
async def ollama_pull_reset():
    logger.info("Ollama pull state reset requested")
    return await reset_pull_state()


@router.delete("/api/v1/ollama")
async def ollama_delete():
    logger.info("Ollama delete requested")
    return await delete_vllm()
