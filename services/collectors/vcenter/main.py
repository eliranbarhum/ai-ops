import asyncio
import os
import logging
import socket
from datetime import datetime, timezone
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("collector-vcenter")

app = FastAPI(title="MCO vCenter Collector", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])
Instrumentator().instrument(app).expose(app)

CONFIG_STORE_URL = os.getenv("CONFIG_STORE_URL", "http://config-store:8009")
_session_token: Optional[str] = None
_last_host: Optional[str] = None
_session_lock: Optional[asyncio.Lock] = None


@app.on_event("startup")
async def _startup():
    global _session_lock
    _session_lock = asyncio.Lock()


async def _get_cfg() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{CONFIG_STORE_URL}/config/raw")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {
            "vcenter_host": os.getenv("VCENTER_HOST", ""),
            "vcenter_user": os.getenv("VCENTER_USER", "administrator@vsphere.local"),
            "vcenter_password": os.getenv("VCENTER_PASSWORD", ""),
            "vcenter_verify_ssl": os.getenv("VCENTER_VERIFY_SSL", "false").lower() == "true",
        }


def _make_client(verify: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=verify, timeout=30.0)


async def _get_session(cfg: dict) -> str:
    global _session_token, _last_host, _session_lock
    host = cfg["vcenter_host"]
    if _session_token and _last_host == host:
        return _session_token
    lock = _session_lock or asyncio.Lock()
    async with lock:
        # Re-check after acquiring lock — another coroutine may have refreshed already
        if _session_token and _last_host == host:
            return _session_token
        async with _make_client(cfg.get("vcenter_verify_ssl", False)) as client:
            resp = await client.post(
                f"https://{host}/api/session",
                auth=(cfg["vcenter_user"], cfg["vcenter_password"]),
            )
            resp.raise_for_status()
            _session_token = resp.json()
            _last_host = host
        return _session_token


async def _vcenter_get(cfg: dict, path: str) -> dict | list:
    global _session_token
    host = cfg["vcenter_host"]
    verify = cfg.get("vcenter_verify_ssl", False)
    token = await _get_session(cfg)
    async with _make_client(verify) as client:
        resp = await client.get(
            f"https://{host}/api{path}",
            headers={"vmware-api-session-id": token},
        )
        if resp.status_code == 401:
            _session_token = None
            token = await _get_session(cfg)
            resp = await client.get(
                f"https://{host}/api{path}",
                headers={"vmware-api-session-id": token},
            )
        resp.raise_for_status()
        return resp.json()


@app.get("/collect/inventory")
async def collect_inventory():
    cfg = await _get_cfg()
    if not cfg.get("vcenter_host"):
        raise HTTPException(status_code=503, detail="vCenter not configured — open Settings to configure")
    try:
        datacenters = await _vcenter_get(cfg, "/vcenter/datacenter")
        clusters = await _vcenter_get(cfg, "/vcenter/cluster")
        hosts = await _vcenter_get(cfg, "/vcenter/host")
        vms = await _vcenter_get(cfg, "/vcenter/vm")
        return {
            "datacenters": datacenters,
            "clusters": clusters,
            "hosts": hosts,
            "vm_count": len(vms) if isinstance(vms, list) else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"vCenter API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/clusters")
async def collect_clusters():
    cfg = await _get_cfg()
    if not cfg.get("vcenter_host"):
        raise HTTPException(status_code=503, detail="vCenter not configured")
    try:
        clusters = await _vcenter_get(cfg, "/vcenter/cluster")
        enriched = []
        for cluster in (clusters if isinstance(clusters, list) else []):
            cluster_id = cluster.get("cluster")
            # The list response carries ha_enabled/drs_enabled as top-level booleans;
            # the old nested detail.get("ha", {}).get("enabled") read always returned False.
            host_count = 0
            try:
                hosts = await _vcenter_get(cfg, f"/vcenter/host?clusters={cluster_id}")
                host_count = len(hosts) if isinstance(hosts, list) else 0
            except Exception:
                pass
            enriched.append({
                "cluster_id": cluster_id,
                "name": cluster.get("name"),
                "ha_enabled": bool(cluster.get("ha_enabled", False)),
                "drs_enabled": bool(cluster.get("drs_enabled", False)),
                "host_count": host_count,
            })
        return {"clusters": enriched, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _resolve_ip(fqdn: str) -> str:
    """Resolve FQDN to IPv4; return empty string on failure."""
    try:
        return socket.gethostbyname(fqdn) if fqdn else ""
    except Exception:
        return ""


@app.get("/collect/hosts")
async def collect_hosts():
    cfg = await _get_cfg()
    if not cfg.get("vcenter_host"):
        raise HTTPException(status_code=503, detail="vCenter not configured")
    try:
        hosts = await _vcenter_get(cfg, "/vcenter/host")
        loop = asyncio.get_event_loop()
        enriched = []
        for host in (hosts if isinstance(hosts, list) else []):
            fqdn = host.get("name", "")
            # Resolve management IP from FQDN — used by discovery engine and fleet enrichment
            mgmt_ip = await loop.run_in_executor(None, _resolve_ip, fqdn) if fqdn else ""
            enriched.append({
                "host_id": host.get("host"),
                "name": fqdn,
                "management_ip": mgmt_ip,
                "connection_state": host.get("connection_state"),
                "power_state": host.get("power_state"),
                "esxi_version": "",
                "esxi_build": "",
                "cpu_model": "",
                "cpu_sockets": 0,
                "cpu_cores": 0,
                "cpu_threads": 0,
                "memory_gb": 0,
            })
        return {"hosts": enriched, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/datastores")
async def collect_datastores():
    cfg = await _get_cfg()
    if not cfg.get("vcenter_host"):
        raise HTTPException(status_code=503, detail="vCenter not configured")
    try:
        datastores = await _vcenter_get(cfg, "/vcenter/datastore")
        enriched = []
        for ds in (datastores if isinstance(datastores, list) else []):
            ds_id = ds.get("datastore")
            detail = {}
            try:
                detail = await _vcenter_get(cfg, f"/vcenter/datastore/{ds_id}")
            except Exception:
                pass

            # capacity is in the list summary; free_space may also be in list or detail
            cap_bytes = int(ds.get("capacity", 0) or detail.get("capacity", 0) or 0)
            free_bytes = int(ds.get("free_space", 0) or detail.get("free_space", 0) or 0)
            cap_gb = round(cap_bytes / (1024 ** 3), 1)
            free_gb = round(free_bytes / (1024 ** 3), 1)
            used_pct = round(((cap_gb - free_gb) / cap_gb) * 100, 1) if cap_gb > 0 else 0

            enriched.append({
                "datastore_id": ds_id,
                "name": ds.get("name"),
                "type": ds.get("type", ""),
                "capacity_gb": cap_gb,
                "free_gb": free_gb,
                "used_pct": used_pct,
            })
        return {"datastores": enriched, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/networks")
async def collect_networks():
    cfg = await _get_cfg()
    if not cfg.get("vcenter_host"):
        raise HTTPException(status_code=503, detail="vCenter not configured")
    try:
        networks = await _vcenter_get(cfg, "/vcenter/network")
        result = [
            {
                "name": n.get("name"),
                "type": n.get("type", ""),
                "network": n.get("network", ""),
            }
            for n in (networks if isinstance(networks, list) else [])
        ]
        return {"networks": result, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/versions")
async def collect_versions():
    cfg = await _get_cfg()
    if not cfg.get("vcenter_host"):
        raise HTTPException(status_code=503, detail="vCenter not configured")

    vcenter_ver = "unknown"
    esxi_ver = "unknown"

    # VCF 9.x: appliance/system/version (vcenter/system/version returns 404)
    for path in ("/appliance/system/version", "/vcenter/system/version"):
        try:
            about = await _vcenter_get(cfg, path)
            vcenter_ver = about.get("version", "unknown")
            if vcenter_ver != "unknown":
                break
        except Exception as e:
            logger.warning(f"collect/versions: version path {path} failed: {e}")

    # ESXi version: use summary endpoint (host detail/{id} returns 404 on VCF 9.x)
    try:
        hosts = await _vcenter_get(cfg, "/vcenter/host")
        host_versions: set[str] = set()
        for h in (hosts if isinstance(hosts, list) else []):
            ver = h.get("version", "") or h.get("esxi_version", "")
            if ver:
                host_versions.add(ver)
        if host_versions:
            esxi_ver = min(host_versions)
    except Exception as e:
        logger.warning(f"collect/versions: ESXi version collection failed: {e}")

    return {
        "versions": {"vcenter": vcenter_ver, "esxi": esxi_ver},
        "vcenter_fqdn": cfg.get("vcenter_host", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def _get_cluster_ns_stats(cfg: dict) -> dict:
    """
    Returns per-cluster CPU/RAM stats from the namespace-management API.
    Keyed by cluster name. Available in VCF 9.x where /vcenter/host/{id} returns 404.
    stats units: cpu_used/cpu_capacity in MHz, memory_used/memory_capacity in KB.
    """
    try:
        clusters = await _vcenter_get(cfg, "/vcenter/namespace-management/clusters")
        result = {}
        for c in (clusters if isinstance(clusters, list) else []):
            name = c.get("cluster_name", "")
            stats = c.get("stats", {})
            result[name] = stats
        return result
    except Exception:
        return {}


@app.get("/collect/cluster-metrics")
async def collect_cluster_metrics():
    """
    Cluster-level CPU and RAM utilisation from vCenter namespace-management API.
    Fallback when vROps is unavailable. Returns format compatible with data_router.
    """
    cfg = await _get_cfg()
    if not cfg.get("vcenter_host"):
        raise HTTPException(status_code=503, detail="vCenter not configured")
    try:
        clusters_raw = await _vcenter_get(cfg, "/vcenter/namespace-management/clusters")
        resources = []
        for c in (clusters_raw if isinstance(clusters_raw, list) else []):
            name = c.get("cluster_name", "")
            stats = c.get("stats", {})
            cpu_used = float(stats.get("cpu_used", 0) or 0)
            cpu_cap  = float(stats.get("cpu_capacity", 0) or 0)
            mem_used = float(stats.get("memory_used", 0) or 0)
            mem_cap  = float(stats.get("memory_capacity", 0) or 0)
            resources.append({
                "name":       name,
                "cpu_usage":  round((cpu_used / cpu_cap) * 100, 1) if cpu_cap > 0 else 0.0,
                "ram_usage":  round((mem_used / mem_cap) * 100, 1) if mem_cap > 0 else 0.0,
                "cpu_mhz_total":    cpu_cap,
                "memory_kb_total":  mem_cap,
                "_source": "vcenter-ns",
            })
        return {"resources": resources, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/host-hardware")
async def collect_host_hardware():
    """
    Per-host CPU, RAM, and usage stats.
    VCF 9.x: /api/vcenter/host/{id} returns 404, so we distribute cluster-level
    namespace-management stats equally across hosts as a best-effort fallback.
    """
    cfg = await _get_cfg()
    if not cfg.get("vcenter_host"):
        raise HTTPException(status_code=503, detail="vCenter not configured")
    try:
        hosts_raw, clusters_raw = await asyncio.gather(
            _vcenter_get(cfg, "/vcenter/host"),
            _vcenter_get(cfg, "/vcenter/namespace-management/clusters"),
            return_exceptions=True,
        )
        hosts_list = hosts_raw if isinstance(hosts_raw, list) else []

        # Build per-cluster stats keyed by cluster name
        ns_stats: dict = {}
        if isinstance(clusters_raw, list):
            for c in clusters_raw:
                ns_stats[c.get("cluster_name", "")] = c.get("stats", {})

        # We need cluster→hosts membership to do per-cluster distribution.
        # vCenter host list doesn't tell us which cluster a host belongs to,
        # so for now split the first (and usually only) cluster across all hosts.
        total_hosts = len(hosts_list)
        # Sum all cluster stats for the cluster pool this vCenter manages
        total_mem_kb = sum(float(s.get("memory_capacity", 0) or 0) for s in ns_stats.values())
        total_mem_used_kb = sum(float(s.get("memory_used", 0) or 0) for s in ns_stats.values())
        total_cpu_mhz = sum(float(s.get("cpu_capacity", 0) or 0) for s in ns_stats.values())
        total_cpu_used_mhz = sum(float(s.get("cpu_used", 0) or 0) for s in ns_stats.values())

        mem_gb_per_host  = round((total_mem_kb / (1024 ** 2)) / total_hosts, 1) if total_hosts > 0 and total_mem_kb > 0 else 0
        mem_used_kb_each = total_mem_used_kb / total_hosts if total_hosts > 0 else 0
        mem_cap_kb_each  = total_mem_kb / total_hosts if total_hosts > 0 else 0
        cpu_used_each    = total_cpu_used_mhz / total_hosts if total_hosts > 0 else 0
        cpu_cap_each     = total_cpu_mhz / total_hosts if total_hosts > 0 else 0

        results = []
        for h in hosts_list:
            name = h.get("name", "")
            # Try /vcenter/host/{id} first — returns 404 on most VCF 9.x builds
            host_id = h.get("host", "")
            cpu_cores, mem_from_detail = 0, 0.0
            try:
                if host_id:
                    detail = await _vcenter_get(cfg, f"/vcenter/host/{host_id}")
                    cpu = detail.get("cpu", {})
                    mem = detail.get("memory", {})
                    sockets   = int(cpu.get("count", 0) or 0)
                    cores_ps  = int(cpu.get("cores_per_socket", 0) or 0)
                    cpu_cores = sockets * cores_ps
                    mem_from_detail = float(mem.get("size_MiB", 0) or 0) / 1024
            except Exception:
                pass

            memory_gb = mem_from_detail if mem_from_detail > 0 else mem_gb_per_host
            cpu_usage = round((cpu_used_each / cpu_cap_each) * 100, 1) if cpu_cap_each > 0 else 0.0
            ram_usage = round((mem_used_kb_each / mem_cap_kb_each) * 100, 1) if mem_cap_kb_each > 0 else 0.0

            results.append({
                "name":       name,
                "cpu_cores":  cpu_cores,
                "memory_gb":  memory_gb,
                "cpu_usage":  cpu_usage,
                "ram_usage":  ram_usage,
                "cpu_model":  "",
                "_source":    "vcenter-ns" if memory_gb > 0 else "vcenter",
            })

        return {"hosts": results, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/host/{host_id}")
async def collect_host_detail(host_id: str):
    """
    Per-host detail: cluster membership, VMs, and datastores.
    VCF 9.x does not support filter.hosts on /vm or /datastore (returns 400);
    instead we discover the host's cluster via /vcenter/host?filter.clusters={id}
    and return VMs/datastores at the cluster scope.
    """
    cfg = await _get_cfg()
    if not cfg.get("vcenter_host"):
        raise HTTPException(status_code=503, detail="vCenter not configured")
    try:
        clusters_list, all_vms, all_datastores = await asyncio.gather(
            _vcenter_get(cfg, "/vcenter/cluster"),
            _vcenter_get(cfg, "/vcenter/vm"),
            _vcenter_get(cfg, "/vcenter/datastore"),
            return_exceptions=True,
        )

        # Cluster membership — VCF 9.x REST API does not expose per-host cluster assignment:
        # filter.clusters returns 400, cluster detail has no hosts array.
        # With a single cluster (typical VCF), all hosts belong to it.
        cluster_id, cluster_name = "", ""
        if isinstance(clusters_list, list) and len(clusters_list) == 1:
            cluster_id = clusters_list[0].get("cluster", "")
            cluster_name = clusters_list[0].get("name", "")
        elif isinstance(clusters_list, list) and len(clusters_list) > 1:
            # Multi-cluster: try filter.clusters (may work on some versions)
            for cl in clusters_list:
                cl_id = cl.get("cluster", "")
                try:
                    hosts_in_cl = await _vcenter_get(cfg, f"/vcenter/host?filter.clusters={cl_id}")
                    if isinstance(hosts_in_cl, list) and any(h.get("host") == host_id for h in hosts_in_cl):
                        cluster_id = cl_id
                        cluster_name = cl.get("name", "")
                        break
                except Exception:
                    pass

        # VMs — all VMs (VCF 9.x filter.hosts returns 400; show cluster scope if known)
        vms = []
        if isinstance(all_vms, list):
            for vm in all_vms[:60]:
                vms.append({
                    "vm_id": vm.get("vm", ""),
                    "name": vm.get("name", ""),
                    "power_state": vm.get("power_state", ""),
                    "memory_size_MiB": vm.get("memory_size_MiB", 0),
                    "cpu_count": vm.get("cpu_count", 0),
                })

        # Datastores — all (filter.hosts returns 400 on VCF 9.x)
        datastores = []
        if isinstance(all_datastores, list):
            for ds in all_datastores:
                cap_b = int(ds.get("capacity", 0) or 0)
                free_b = int(ds.get("free_space", 0) or 0)
                cap_gb = round(cap_b / (1024 ** 3), 1)
                free_gb = round(free_b / (1024 ** 3), 1)
                datastores.append({
                    "datastore_id": ds.get("datastore", ""),
                    "name": ds.get("name", ""),
                    "type": ds.get("type", ""),
                    "capacity_gb": cap_gb,
                    "free_gb": free_gb,
                    "used_pct": round(((cap_gb - free_gb) / cap_gb) * 100, 1) if cap_gb > 0 else 0,
                })

        return {
            "host_id": host_id,
            "cluster_id": cluster_id,
            "cluster_name": cluster_name,
            "vm_count": len(vms),
            "vm_scope": "cluster" if cluster_id else "environment",
            "vms": vms,
            "datastores": datastores,
            "vmkernel_adapters": [],  # Not exposed in vCenter REST API for VCF 9.x
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/collect/network/{network_id}")
async def collect_network_detail(network_id: str):
    cfg = await _get_cfg()
    if not cfg.get("vcenter_host"):
        raise HTTPException(status_code=503, detail="vCenter not configured")
    try:
        networks = await _vcenter_get(cfg, "/vcenter/network")
        network = next(
            (n for n in (networks if isinstance(networks, list) else []) if n.get("network") == network_id),
            None,
        )

        vm_count: int | None = None
        host_count: int | None = None
        vm_filter_supported = False

        # Try per-network VM filtering (returns 400 on VCF 9.x — handled gracefully)
        try:
            vms = await _vcenter_get(cfg, f"/vcenter/vm?filter.networks={network_id}")
            vm_count = len(vms) if isinstance(vms, list) else None
            vm_filter_supported = True
        except Exception:
            pass

        try:
            hosts = await _vcenter_get(cfg, f"/vcenter/host?filter.networks={network_id}")
            host_count = len(hosts) if isinstance(hosts, list) else None
        except Exception:
            pass

        return {
            "network_id": network_id,
            "name": network.get("name") if network else network_id,
            "type": network.get("type", "") if network else "",
            "vm_count": vm_count,
            "host_count": host_count,
            "vm_filter_supported": vm_filter_supported,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    cfg = await _get_cfg()
    return {"status": "healthy", "service": "collector-vcenter", "vcenter_configured": bool(cfg.get("vcenter_host"))}
