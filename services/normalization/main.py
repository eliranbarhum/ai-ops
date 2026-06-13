import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator
from normalizers import normalize_vcenter_inventory, normalize_cluster_capacity, normalize_esxi_metrics
from normalizers import normalize_vrops_metrics, normalize_logs, normalize_network_metrics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("normalization")

app = FastAPI(title="MCO Normalization Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
Instrumentator().instrument(app).expose(app)

NORMALIZER_MAP = {
    "vcenter_inventory": normalize_vcenter_inventory,
    "cluster_capacity": normalize_cluster_capacity,
    "esxi_metrics": normalize_esxi_metrics,
    "vrops_metrics": normalize_vrops_metrics,
    "logs": normalize_logs,
    "network_metrics": normalize_network_metrics,
}


class NormalizeRequest(BaseModel):
    source: str
    data: dict | list


@app.post("/normalize")
async def normalize(request: NormalizeRequest):
    normalizer = NORMALIZER_MAP.get(request.source)
    if not normalizer:
        raise HTTPException(status_code=400, detail=f"Unknown source: {request.source}")
    try:
        result = normalizer(request.data)
        return result
    except Exception as e:
        logger.error(f"Normalization error for source={request.source}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "normalization"}
