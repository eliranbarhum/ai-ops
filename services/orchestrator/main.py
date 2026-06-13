import os
import asyncio
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator
from pipeline import run_pipeline, run_pipeline_stream

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orchestrator")

app = FastAPI(title="MCO Orchestrator", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])
Instrumentator().instrument(app).expose(app)


class OrchestrationRequest(BaseModel):
    query: str
    target: str = "vcf_readiness"


@app.post("/orchestrate")
async def orchestrate(request: OrchestrationRequest):
    logger.info(f"Orchestrating: target={request.target} query={request.query!r}")
    result = await run_pipeline(request.target, request.query)
    return result


@app.post("/orchestrate/stream")
async def orchestrate_stream(request: OrchestrationRequest):
    logger.info(f"Streaming pipeline: target={request.target} query={request.query!r}")
    return StreamingResponse(
        run_pipeline_stream(request.target, request.query),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "orchestrator"}
