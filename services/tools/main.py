import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from tools.vcenter_inventory import get_vcenter_inventory
from tools.cluster_capacity import get_cluster_capacity
from tools.esxi_metrics import get_esxi_metrics
from tools.vrops_metrics import get_vrops_metrics
from tools.query_logs import query_logs
from tools.vcf_compatibility import check_vcf_compatibility
from tools.network_metrics import get_network_metrics
from tools.broadcom_interop import check_broadcom_interop
from tools.env_manifest import get_env_manifest
from tools.sddc_health import get_sddc_health
from tools.discovery_assets import get_discovery_assets
from tools.datastore_capacity import get_datastore_capacity
from tool_cache import cached_call, invalidate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tool-service")

app = FastAPI(title="MCO Tool Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])
Instrumentator().instrument(app).expose(app)

TOOL_REGISTRY = {
    "get_vcenter_inventory": get_vcenter_inventory,
    "get_cluster_capacity":  get_cluster_capacity,
    "get_esxi_metrics":      get_esxi_metrics,
    "get_vrops_metrics":     get_vrops_metrics,
    "query_logs":            query_logs,
    "check_vcf_compatibility": check_vcf_compatibility,
    "get_network_metrics":   get_network_metrics,
    "check_broadcom_interop": check_broadcom_interop,
    "get_env_manifest":      get_env_manifest,
    "get_sddc_health":       get_sddc_health,
    "get_discovery_assets":  get_discovery_assets,
    "get_datastore_capacity": get_datastore_capacity,
}


@app.post("/tools/{tool_name}")
async def execute_tool(tool_name: str):
    if tool_name not in TOOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    logger.info(f"Executing tool: {tool_name}")
    try:
        result = await cached_call(tool_name, TOOL_REGISTRY[tool_name])
        return result
    except Exception as e:
        logger.error(f"Tool {tool_name} raised: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/tools/cache")
async def clear_cache():
    """Force-invalidate all tool caches (useful after config changes)."""
    invalidate()
    return {"ok": True, "message": "All tool caches cleared"}


@app.get("/tools")
async def list_tools():
    return {"tools": list(TOOL_REGISTRY.keys())}


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "tool-service"}
