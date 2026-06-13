import json as _json
import logging
import os
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from shared import ORCHESTRATOR_URL
from metrics import pipeline_runs

_REDIS_URL = os.getenv("REDIS_URL", "")

logger = logging.getLogger("api-gateway")
router = APIRouter()


def _publish_analysis_event(result: dict) -> None:
    if not _REDIS_URL:
        return
    import asyncio
    score = result.get("readiness_score", 100)
    status = result.get("status", "UNKNOWN")
    event = {
        "type": "score_critical" if status in ("NOT_READY", "CRITICAL") or score < 40 else "score_update",
        "readiness_score": score,
        "status": status,
        "summary": f"VCF readiness score is {score}/100 ({status})",
        "severity": "critical" if score < 40 else "warning" if score < 70 else "info",
    }
    async def _pub():
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(_REDIS_URL, decode_responses=True)
            await client.publish("mco:events", _json.dumps(event))
        except Exception:
            pass
    asyncio.create_task(_pub())


class AnalysisRequest(BaseModel):
    query: str = "Run VCF readiness analysis"
    target: str = "vcf_readiness"


class RiskFactor(BaseModel):
    severity: str
    message: str
    component: str


class Evidence(BaseModel):
    source: str
    metric: str
    value: str
    threshold: str | None = None


class AnalysisResponse(BaseModel):
    readiness_score: int
    status: str
    risk_factors: list[RiskFactor]
    recommendations: list[str]
    sub_scores: list[dict] = []
    evidence: list[Evidence]
    explanation: str
    raw_metrics: dict


@router.post("/api/v1/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest):
    logger.info(f"Incoming analysis request: target={request.target}")
    async with httpx.AsyncClient(timeout=660.0) as client:
        try:
            resp = await client.post(f"{ORCHESTRATOR_URL}/orchestrate", json=request.model_dump())
            resp.raise_for_status()
            pipeline_runs.labels(target=request.target, status="success").inc()
            result = resp.json()
            _publish_analysis_event(result)
            return result
        except httpx.TimeoutException:
            pipeline_runs.labels(target=request.target, status="timeout").inc()
            raise HTTPException(status_code=504, detail="Orchestrator timed out")
        except httpx.HTTPStatusError as e:
            pipeline_runs.labels(target=request.target, status="error").inc()
            raise HTTPException(status_code=502, detail=f"Orchestrator error: {e.response.text}")
        except httpx.RequestError as e:
            pipeline_runs.labels(target=request.target, status="error").inc()
            raise HTTPException(status_code=503, detail="Orchestrator unreachable")


@router.post("/api/v1/analyze/stream")
async def analyze_stream(request: AnalysisRequest):
    async def _proxy():
        async with httpx.AsyncClient(timeout=660.0) as client:
            try:
                async with client.stream(
                    "POST", f"{ORCHESTRATOR_URL}/orchestrate/stream",
                    json=request.model_dump(),
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            yield f"{line}\n\n"
            except Exception as e:
                logger.error(f"analyze_stream proxy error: {e}")
                yield f"data: {_json.dumps({'type': 'error', 'message': 'Analysis failed'})}\n\n"
                yield f"data: {_json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        _proxy(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
