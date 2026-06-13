import json
import logging
import os
from datetime import datetime, timezone

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator
from scorer import compute_score, CPU_WARN, CPU_CRIT, RAM_WARN, RAM_CRIT, LATENCY_WARN_MS, LATENCY_CRIT_MS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scoring-engine")

POSTGRES_URL = os.getenv("POSTGRES_URL", "")

app = FastAPI(title="MCO Scoring Engine", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])
Instrumentator().instrument(app).expose(app)

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool | None:
    global _pool
    if _pool is not None:
        return _pool
    if not POSTGRES_URL:
        return None
    try:
        _pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=5, command_timeout=10)
        logger.info("TimescaleDB pool created")
    except Exception as e:
        logger.warning("TimescaleDB unavailable: %s", e)
        return None
    return _pool


@app.on_event("startup")
async def startup():
    await _get_pool()


class ScoringRequest(BaseModel):
    tools_data: dict
    target: str = "vcf_readiness"


@app.post("/score")
async def score(request: ScoringRequest):
    logger.info(f"Scoring request: target={request.target}, tools={list(request.tools_data.keys())}")
    result = compute_score(request.tools_data, request.target)
    logger.info(f"Score result: {result['readiness_score']} / {result['status']}")

    pool = await _get_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO scoring_history
                       (time, target, readiness_score, status, sub_scores, risk_factor_count)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    datetime.now(timezone.utc),
                    request.target,
                    result["readiness_score"],
                    result["status"],
                    json.dumps(result.get("sub_scores", [])),
                    len(result.get("risk_factors", [])),
                )
        except Exception as e:
            logger.warning(f"Could not persist score history: {e}")

    return result


@app.get("/history")
async def history(limit: int = 50):
    pool = await _get_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, time AS timestamp, target, readiness_score, status, sub_scores, risk_factor_count
                       FROM scoring_history
                       ORDER BY time DESC
                       LIMIT $1""",
                    min(limit, 200),
                )
            result = []
            for row in rows:
                r = dict(row)
                r["timestamp"] = r["timestamp"].isoformat() if r["timestamp"] else ""
                try:
                    r["sub_scores"] = json.loads(r["sub_scores"] or "[]")
                except Exception:
                    r["sub_scores"] = []
                result.append(r)
            return {"history": result}
        except Exception as e:
            logger.warning(f"Could not read score history: {e}")

    return {"history": []}


@app.get("/diff")
async def score_diff():
    """Return delta between the two most recent scoring runs (same target)."""
    pool = await _get_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, time AS timestamp, target, readiness_score, status, sub_scores
                       FROM scoring_history
                       ORDER BY time DESC LIMIT 2""",
                )
            if len(rows) < 2:
                return {"diff": None, "reason": "not enough history"}
            cur, prev = [dict(r) for r in rows]
            for r in (cur, prev):
                r["timestamp"] = r["timestamp"].isoformat() if r["timestamp"] else ""
                try:
                    r["sub_scores"] = json.loads(r["sub_scores"] or "[]")
                except Exception:
                    r["sub_scores"] = []
            delta = cur["readiness_score"] - prev["readiness_score"]
            sub_deltas = {}
            prev_subs = {s["name"]: s["score"] for s in prev["sub_scores"]}
            for s in cur["sub_scores"]:
                prev_val = prev_subs.get(s["name"])
                if prev_val is not None:
                    sub_deltas[s["name"]] = s["score"] - prev_val
            return {
                "diff": {
                    "current":  {"score": cur["readiness_score"],  "status": cur["status"],  "timestamp": cur["timestamp"]},
                    "previous": {"score": prev["readiness_score"], "status": prev["status"], "timestamp": prev["timestamp"]},
                    "delta": delta,
                    "sub_deltas": sub_deltas,
                }
            }
        except Exception as e:
            logger.warning(f"score diff failed: {e}")
    return {"diff": None, "reason": "no database"}


@app.get("/thresholds")
async def thresholds():
    return {
        "cpu_warn": CPU_WARN, "cpu_crit": CPU_CRIT,
        "ram_warn": RAM_WARN, "ram_crit": RAM_CRIT,
        "latency_warn_ms": LATENCY_WARN_MS, "latency_crit_ms": LATENCY_CRIT_MS,
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "scoring-engine"}
