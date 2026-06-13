import asyncio
import logging
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from routers import explain, generate, agent_routes, mcp_routes, health
from providers import _get_cfg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm-gateway")


async def _warmup_ollama():
    await asyncio.sleep(5)
    try:
        cfg = await _get_cfg()
        url = (cfg.get("vllm_url") or "http://vllm-server:11434").rstrip("/")
        model = cfg.get("vllm_model") or "qwen2.5-coder:7b"
        async with httpx.AsyncClient(timeout=300.0) as client:
            ps = await client.get(f"{url}/api/ps")
            loaded = [m["name"] for m in ps.json().get("models", [])]
            if model in loaded:
                logger.info("ollama warm-up: %s already loaded", model)
                return
            logger.info("ollama warm-up: loading %s into RAM…", model)
            await client.post(f"{url}/api/generate",
                              json={"model": model, "prompt": "", "keep_alive": -1, "stream": False})
            logger.info("ollama warm-up: %s ready", model)
    except Exception as e:
        logger.warning("ollama warm-up failed (will load on first request): %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from rag import retriever, vcf_docs, sddc_api
    logger.info(
        "BM25 RAG ready: %d vCenter endpoints, %d VCF doc chunks, %d SDDC chunks",
        len(retriever.endpoints), len(vcf_docs.chunks), len(sddc_api.chunks),
    )
    asyncio.create_task(_warmup_ollama())
    yield


app = FastAPI(title="MCO LLM Gateway", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])
Instrumentator().instrument(app).expose(app)

app.include_router(explain.router)
app.include_router(generate.router)
app.include_router(agent_routes.router)
app.include_router(mcp_routes.router)
app.include_router(health.router)
