import asyncio
import os
import logging
from datetime import datetime, timezone
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("collector-vrops")

app = FastAPI(title="MCO VCF Operations Collector", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])
Instrumentator().instrument(app).expose(app)

CONFIG_STORE_URL = os.getenv("CONFIG_STORE_URL", "http://config-store:8009")
_auth_token: Optional[str] = None
_last_host: Optional[str] = None


async def _get_cfg() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{CONFIG_STORE_URL}/config/raw")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {
            "vrops_host": os.getenv("VROPS_HOST", ""),
            "vrops_user": os.getenv("VROPS_USER", "admin"),
            "vrops_password": os.getenv("VROPS_PASSWORD", ""),
            "vrops_verify_ssl": os.getenv("VROPS_VERIFY_SSL", "false").lower() == "true",
        }


def _make_client(verify: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=verify, timeout=30.0)


async def _get_token(cfg: dict) -> str:
    global _auth_token, _last_host
    host = cfg["vrops_host"]
    if _auth_token and _last_host == host:
        return _auth_token
    async with _make_client(cfg.get("vrops_verify_ssl", False)) as client:
        resp = await client.post(
            f"https://{host}/suite-api/api/auth/token/acquire",
            json={"username": cfg["vrops_user"], "password": cfg["vrops_password"], "authSource": "LOCAL"},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        resp.raise_for_status()
        _auth_token = resp.json().get("token")
        _last_host = host
        logger.info("vROps token acquired")
        return _auth_token


async def _vrops_get(cfg: dict, path: str, params: dict | None = None) -> dict:
    global _auth_token
    host = cfg["vrops_host"]
    verify = cfg.get("vrops_verify_ssl", False)
    token = await _get_token(cfg)
    async with _make_client(verify) as client:
        resp = await client.get(
            f"https://{host}/suite-api/api{path}",
            headers={"Authorization": f"vRealizeOpsToken {token}", "Accept": "application/json"},
            params=params,
        )
        if resp.status_code == 401:
            _auth_token = None
            token = await _get_token(cfg)
            resp = await client.get(
                f"https://{host}/suite-api/api{path}",
                headers={"Authorization": f"vRealizeOpsToken {token}", "Accept": "application/json"},
                params=params,
            )
        resp.raise_for_status()
        return resp.json()


def _extract_stat(stat_list: list, key: str, default: float | None = 0.0) -> float | None:
    """Latest value for a statKey; `default` (0.0 or None) when the key is absent."""
    for s in stat_list:
        if s.get("statKey", {}).get("key") == key:
            data = s.get("data", [])
            return round(data[-1], 2) if data else default
    return default


@app.get("/collect/metrics")
async def collect_metrics():
    cfg = await _get_cfg()
    if not cfg.get("vrops_host"):
        raise HTTPException(status_code=503, detail="VCF Operations not configured — open Settings to configure")
    try:
        resources = await _vrops_get(cfg, "/resources", params={
            "resourceKind": "ClusterComputeResource",
            "pageSize": 50,
        })
        resource_list = resources.get("resourceList", [])
        metrics_data = []
        for resource in resource_list[:20]:
            res_id = resource.get("identifier")
            res_name = resource.get("resourceKey", {}).get("name", "unknown")
            try:
                # Fetch all latest stats (no filter) — statKey filter on /stats/latest
                # returns empty values if the key format doesn't match exactly.
                stats = await _vrops_get(cfg, f"/resources/{res_id}/stats/latest")
                values = stats.get("values") or []
                stat_list = (values[0] if values else {}).get("stat-list", {}).get("stat", [])

                # cpu|usage_average = true utilization %. cpu|workload is a demand
                # badge (can exceed 100) — keep only as fallback, and say which we used.
                cpu_usage = _extract_stat(stat_list, "cpu|usage_average")
                cpu_metric = "cpu|usage_average"
                if cpu_usage == 0.0:
                    cpu_usage = _extract_stat(stat_list, "cpu|workload")
                    cpu_metric = "cpu|workload"

                # mem|usage_average = consumed % — what capacity planning needs.
                # mem|active is the touch-rate estimate and far lower; keep it as a
                # separate field so callers can show both.
                ram_usage = _extract_stat(stat_list, "mem|usage_average")
                ram_metric = "mem|usage_average"
                mem_active = _extract_stat(stat_list, "mem|active_average")
                mem_total = _extract_stat(stat_list, "mem|totalCapacity_average")
                ram_active_pct = round((mem_active / mem_total) * 100, 2) if mem_total > 0 else None
                if ram_usage == 0.0 and ram_active_pct is not None:
                    ram_usage = ram_active_pct
                    ram_metric = "mem|active/totalCapacity"

                # Disk latency lives under datastore|/disk| — diskspace| is capacity
                # and has no latency key (the old code always read 0 from it).
                latency = None
                for lat_key in ("datastore|totalLatency_average", "disk|totalLatency_average"):
                    v = _extract_stat(stat_list, lat_key, default=None)
                    if v is not None:
                        latency = v
                        break

                metrics_data.append({
                    "resource_id": res_id,
                    "name": res_name,
                    "cpu_usage": cpu_usage,
                    "ram_usage": ram_usage,
                    "ram_active_pct": ram_active_pct,
                    "storage_latency_ms": latency,
                    "_metrics_used": {"cpu": cpu_metric, "ram": ram_metric},
                })
            except Exception as e:
                logger.warning(f"Could not fetch stats for {res_name}: {e}")
                metrics_data.append({"resource_id": res_id, "name": res_name,
                                     "cpu_usage": None, "ram_usage": None, "storage_latency_ms": None})

        return {"resources": metrics_data, "timestamp": datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"vROps API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/cluster-metrics")
async def collect_cluster_metrics():
    """Per-cluster CPU, RAM and storage metrics — used by the /api/v1/fleet aggregator."""
    return await collect_metrics()


@app.get("/collect/component-versions")
async def collect_component_versions():
    """
    Returns management plane appliances discovered by VCF Operations (vROps),
    including FQDN and version for each component type.

    vROps resource kinds used:
      VirtualCenter   → vCenter Server
      NSXTManager     → NSX-T Manager nodes
      SDDCManager     → SDDC Manager (requires VCF adapter)
      vRealize Operations Manager → vROps itself
      vRealizeLogInsight          → Aria Logs / Log Insight
    """
    cfg = await _get_cfg()
    if not cfg.get("vrops_host"):
        raise HTTPException(status_code=503, detail="VCF Operations not configured")
    try:
        # Map our internal key → vROps resourceKind
        KINDS: dict[str, str] = {
            "vcenter":        "VirtualCenter",
            "nsx_manager":    "NSXTManager",
            "sddc_manager":   "SDDCManager",
            "vcf_operations": "vRealize Operations Manager",
            "vcf_logs":       "vRealizeLogInsight",
        }

        versions: dict[str, list] = {}
        for key, kind in KINDS.items():
            try:
                resources = await _vrops_get(cfg, "/resources", params={"resourceKind": kind, "pageSize": 10})
                items = []
                for r in resources.get("resourceList", []):
                    rid = r.get("identifier")
                    name = r.get("resourceKey", {}).get("name", "")
                    fqdn = name
                    version = ""
                    build = ""

                    # Fetch resource properties for FQDN and version strings.
                    # Property keys differ by adapter but share common names.
                    try:
                        props_raw = await _vrops_get(cfg, f"/resources/{rid}/properties")
                        prop_map = {
                            p["name"]: p.get("value", "")
                            for p in props_raw.get("property", [])
                        }
                        fqdn = (prop_map.get("summary|hostname")
                                or prop_map.get("config|hostname")
                                or name)
                        version = (prop_map.get("summary|version")
                                   or prop_map.get("summary|fullName")
                                   or prop_map.get("config|version")
                                   or "")
                        build = prop_map.get("summary|build") or prop_map.get("config|build") or ""
                    except Exception:
                        pass

                    items.append({"name": name, "fqdn": fqdn, "version": version, "build": build})
                versions[key] = items
            except Exception as ex:
                logger.warning(f"Could not fetch {kind} resources: {ex}")
                versions[key] = []

        return {"versions": versions, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/network")
async def collect_network():
    cfg = await _get_cfg()
    if not cfg.get("vrops_host"):
        raise HTTPException(status_code=503, detail="VCF Operations not configured")
    try:
        resources = await _vrops_get(cfg, "/resources", params={"resourceKind": "NSXTLogicalSwitch", "pageSize": 20})
        network_data = []
        for resource in resources.get("resourceList", [])[:20]:
            res_id = resource.get("identifier")
            res_name = resource.get("resourceKey", {}).get("name", "unknown")
            try:
                stats = await _vrops_get(
                    cfg,
                    f"/resources/{res_id}/stats",
                    params={"statKey": "net|packetTx_droppedRate,net|throughput,net|latency"},
                )
                stat_list = (stats.get("values") or [{}])[0].get("stat-list", {}).get("stat", [])
                network_data.append({
                    "resource_id": res_id,
                    "name": res_name,
                    "packet_loss_pct": _extract_stat(stat_list, "net|packetTx_droppedRate"),
                    "throughput_mbps": _extract_stat(stat_list, "net|throughput"),
                    "latency_ms": _extract_stat(stat_list, "net|latency"),
                })
            except Exception:
                network_data.append({"resource_id": res_id, "name": res_name})
        return {"network_resources": network_data, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/host-details")
async def collect_host_details():
    """Host-level metrics from vROps — CPU/RAM usage per ESXi host."""
    cfg = await _get_cfg()
    if not cfg.get("vrops_host"):
        raise HTTPException(status_code=503, detail="VCF Operations not configured")
    try:
        resources = await _vrops_get(cfg, "/resources", params={
            "resourceKind": "HostSystem",
            "pageSize": 100,
        })
        resource_list = resources.get("resourceList", [])[:50]

        async def _fetch_host(r) -> dict:
            res_id = r.get("identifier")
            name = r.get("resourceKey", {}).get("name", "unknown")
            # Fetch stats and properties in parallel for each host
            stats_result, props_result = await asyncio.gather(
                _vrops_get(cfg, f"/resources/{res_id}/stats/latest"),
                _vrops_get(cfg, f"/resources/{res_id}/properties"),
                return_exceptions=True,
            )
            cpu_usage, ram_usage = 0.0, 0.0
            storage_latency_ms = None
            if isinstance(stats_result, dict):
                values = stats_result.get("values") or []
                stat_list = (values[0] if values else {}).get("stat-list", {}).get("stat", [])
                cpu_usage = _extract_stat(stat_list, "cpu|usage_average")
                ram_usage = _extract_stat(stat_list, "mem|usage_average")
                if cpu_usage == 0.0:
                    cpu_usage = _extract_stat(stat_list, "cpu|workload")
                for lat_key in ("datastore|totalLatency_average", "disk|totalLatency_average"):
                    v = _extract_stat(stat_list, lat_key, default=None)
                    if v is not None:
                        storage_latency_ms = v
                        break

            cpu_model, memory_gb, cpu_sockets, cpu_cores, cpu_threads, esxi_version = "", 0, 0, 0, 0, ""
            if isinstance(props_result, dict):
                prop_map = {p["name"]: p.get("value", "") for p in props_result.get("property", [])}
                vendor = prop_map.get("hardware|vendor", "")
                model = prop_map.get("hardware|vendorModel", "")
                cpu_model = model if (model and vendor and model.startswith(vendor)) else f"{vendor} {model}".strip()
                mem_kb = float(prop_map.get("hardware|memorySize", 0) or 0)
                memory_gb = round(mem_kb / (1024 ** 2))
                cpu_sockets = int(float(prop_map.get("hardware|cpuInfo|numCpuPackages", 0) or 0))
                cpu_cores = int(float(prop_map.get("hardware|cpuInfo|numCpuCores", 0) or 0))
                cpu_threads = cpu_cores
                esxi_version = prop_map.get("summary|version", "")

            return {
                "name": name,
                "cpu_usage": cpu_usage,
                "ram_usage": ram_usage,
                "storage_latency_ms": storage_latency_ms,
                "cpu_model": cpu_model,
                "memory_gb": memory_gb,
                "cpu_sockets": cpu_sockets,
                "cpu_cores": cpu_cores,
                "cpu_threads": cpu_threads,
                "esxi_version": esxi_version,
            }

        # Fetch all hosts in parallel
        hosts = await asyncio.gather(*[_fetch_host(r) for r in resource_list])
        return {"hosts": list(hosts), "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/datastores")
async def collect_datastores():
    """Datastore capacity/free from vROps — fallback when vCenter datastore calls fail."""
    cfg = await _get_cfg()
    if not cfg.get("vrops_host"):
        raise HTTPException(status_code=503, detail="VCF Operations not configured")
    try:
        resources = await _vrops_get(cfg, "/resources", params={"resourceKind": "Datastore", "pageSize": 100})

        async def _fetch_ds(r) -> dict:
            res_id = r.get("identifier")
            name = r.get("resourceKey", {}).get("name", "unknown")
            props_result = await _vrops_get(cfg, f"/resources/{res_id}/properties")
            prop_map = {}
            if isinstance(props_result, dict):
                prop_map = {p["name"]: p.get("value", "") for p in props_result.get("property", [])}
            cap_kb  = float(prop_map.get("diskspace|capacity_inKB", 0) or 0)
            free_kb = float(prop_map.get("diskspace|freeSpace_inKB", 0)
                           or prop_map.get("diskspace|total_freeSpace_inKB", 0) or 0)
            cap_gb  = round(cap_kb  / (1024 ** 2), 1)
            free_gb = round(free_kb / (1024 ** 2), 1)
            used_pct = round(((cap_gb - free_gb) / cap_gb) * 100, 1) if cap_gb > 0 else 0
            return {
                "name": name,
                "type": prop_map.get("summary|type", ""),
                "capacity_gb": cap_gb,
                "free_gb": free_gb,
                "used_pct": used_pct,
                "_source": "vrops",
            }

        ds_list = await asyncio.gather(
            *[_fetch_ds(r) for r in resources.get("resourceList", [])[:100]],
            return_exceptions=True,
        )
        return {
            "datastores": [d for d in ds_list if isinstance(d, dict)],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/networks")
async def collect_networks():
    """DVS portgroups from vROps — fallback when vCenter network calls fail."""
    cfg = await _get_cfg()
    if not cfg.get("vrops_host"):
        raise HTTPException(status_code=503, detail="VCF Operations not configured")
    try:
        pg_resources = await _vrops_get(cfg, "/resources", params={
            "resourceKind": "DistributedVirtualPortgroup", "pageSize": 100,
        })
        networks = []
        for r in pg_resources.get("resourceList", [])[:100]:
            name = r.get("resourceKey", {}).get("name", "unknown")
            networks.append({"name": name, "type": "DISTRIBUTED_PORTGROUP", "_source": "vrops"})
        return {"networks": networks, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    cfg = await _get_cfg()
    return {"status": "healthy", "service": "collector-vrops", "vrops_configured": bool(cfg.get("vrops_host"))}
