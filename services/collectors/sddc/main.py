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
logger = logging.getLogger("collector-sddc")

app = FastAPI(title="MCO SDDC Manager Collector", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET","POST","DELETE","PATCH"], allow_headers=["Content-Type","X-Request-ID"])
Instrumentator().instrument(app).expose(app)

CONFIG_STORE_URL = os.getenv("CONFIG_STORE_URL", "http://config-store:8009")

_sddc_token: Optional[str] = None
_sddc_last_host: Optional[str] = None
_sddc_lock: Optional[asyncio.Lock] = None


@app.on_event("startup")
async def _startup():
    global _sddc_lock
    _sddc_lock = asyncio.Lock()


async def _get_cfg() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{CONFIG_STORE_URL}/config/raw")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {
            "sddc_host": os.getenv("SDDC_HOST", ""),
            "sddc_user": os.getenv("SDDC_USER", "administrator@vsphere.local"),
            "sddc_password": os.getenv("SDDC_PASSWORD", ""),
            "sddc_verify_ssl": os.getenv("SDDC_VERIFY_SSL", "false").lower() == "true",
        }


async def _get_token(cfg: dict) -> str:
    global _sddc_token, _sddc_last_host, _sddc_lock
    host = cfg["sddc_host"]
    if _sddc_token and _sddc_last_host == host:
        return _sddc_token
    lock = _sddc_lock or asyncio.Lock()
    async with lock:
        if _sddc_token and _sddc_last_host == host:
            return _sddc_token
        verify = cfg.get("sddc_verify_ssl", False)
        async with httpx.AsyncClient(verify=verify, timeout=15.0) as client:
            resp = await client.post(
                f"https://{host}/v1/tokens",
                json={"username": cfg["sddc_user"], "password": cfg["sddc_password"]},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            _sddc_token = resp.json()["accessToken"]
            _sddc_last_host = host
    return _sddc_token


async def _sddc_get(cfg: dict, path: str) -> dict | list:
    global _sddc_token
    host = cfg["sddc_host"]
    verify = cfg.get("sddc_verify_ssl", False)
    token = await _get_token(cfg)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with httpx.AsyncClient(verify=verify, timeout=30.0) as client:
        resp = await client.get(f"https://{host}{path}", headers=headers)
        if resp.status_code == 401:
            _sddc_token = None
            token = await _get_token(cfg)
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.get(f"https://{host}{path}", headers=headers)
        resp.raise_for_status()
        return resp.json()


@app.get("/collect/system")
async def collect_system():
    cfg = await _get_cfg()
    if not cfg.get("sddc_host"):
        raise HTTPException(status_code=503, detail="SDDC Manager not configured — open Settings to configure")
    try:
        managers = await _sddc_get(cfg, "/v1/sddc-managers")
        elements = managers.get("elements", [])
        mgr = elements[0] if elements else {}
        return {
            "id": mgr.get("id", ""),
            "fqdn": mgr.get("fqdn", cfg.get("sddc_host", "")),
            "version": mgr.get("version", "unknown"),
            "ip_address": mgr.get("ipAddress", ""),
            "domain": mgr.get("domain", {}).get("name", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"SDDC Manager API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/domains")
async def collect_domains():
    cfg = await _get_cfg()
    if not cfg.get("sddc_host"):
        raise HTTPException(status_code=503, detail="SDDC Manager not configured")
    try:
        data = await _sddc_get(cfg, "/v1/domains")
        domains = []
        for d in data.get("elements", []):
            domains.append({
                "id": d.get("id", ""),
                "name": d.get("name", ""),
                "status": d.get("status", ""),
                "type": d.get("type", ""),
                "upgrade_state": d.get("upgradeState", ""),
                "cluster_count": len(d.get("clusters", [])),
                "vcenter_fqdn": d.get("vcenters", [{}])[0].get("fqdn", "") if d.get("vcenters") else "",
                "nsxt_fqdn": d.get("nsxtCluster", {}).get("nodes", [{}])[0].get("fqdn", "") if d.get("nsxtCluster") else "",
            })
        return {"domains": domains, "timestamp": datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"SDDC Manager API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/hosts")
async def collect_hosts():
    cfg = await _get_cfg()
    if not cfg.get("sddc_host"):
        raise HTTPException(status_code=503, detail="SDDC Manager not configured")
    try:
        data = await _sddc_get(cfg, "/v1/hosts")
        hosts = []
        for h in data.get("elements", []):
            cpu = h.get("cpu", {})
            memory = h.get("memory", {})
            storage = h.get("storage", {})
            hosts.append({
                "id": h.get("id", ""),
                "fqdn": h.get("fqdn", ""),
                "status": h.get("status", ""),
                "esxi_version": h.get("esxiVersion", "unknown"),
                "hardware_vendor": h.get("hardwareVendor", ""),
                "hardware_model": h.get("hardwareModel", ""),
                "cpu_cores": cpu.get("cores", 0),
                "cpu_model": cpu.get("model", ""),
                "memory_gb": round(memory.get("totalCapacityMB", 0) / 1024, 1),
                "storage_tb": round(storage.get("totalCapacityMiB", 0) / (1024 * 1024), 2),
                "cluster_id": h.get("cluster", {}).get("id", "") if h.get("cluster") else "",
                "domain_id": h.get("domain", {}).get("id", "") if h.get("domain") else "",
            })
        return {"hosts": hosts, "host_count": len(hosts), "timestamp": datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"SDDC Manager API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/clusters")
async def collect_clusters():
    cfg = await _get_cfg()
    if not cfg.get("sddc_host"):
        raise HTTPException(status_code=503, detail="SDDC Manager not configured")
    try:
        data = await _sddc_get(cfg, "/v1/clusters")
        clusters = []
        for c in data.get("elements", []):
            clusters.append({
                "id": c.get("id", ""),
                "name": c.get("name", ""),
                "status": c.get("status", ""),
                "host_count": len(c.get("hosts", [])),
                "domain_id": c.get("domain", {}).get("id", "") if c.get("domain") else "",
                "primary_datastore_name": c.get("primaryDatastoreName", ""),
                "primary_datastore_type": c.get("primaryDatastoreType", ""),
                "vsan_enabled": c.get("primaryDatastoreType", "").upper() == "VSAN",
            })
        return {"clusters": clusters, "timestamp": datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"SDDC Manager API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/upgrades")
async def collect_upgrades():
    cfg = await _get_cfg()
    if not cfg.get("sddc_host"):
        raise HTTPException(status_code=503, detail="SDDC Manager not configured")
    try:
        data = await _sddc_get(cfg, "/v1/upgrades")
        upgrades = []
        for u in data.get("elements", []):
            upgrades.append({
                "id": u.get("id", ""),
                "status": u.get("status", ""),
                "type": u.get("type", ""),
                "from_version": u.get("fromVersion", ""),
                "to_version": u.get("toVersion", ""),
                "started_at": u.get("startedAt", ""),
                "completed_at": u.get("completedAt", ""),
            })
        return {"upgrades": upgrades, "timestamp": datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"SDDC Manager API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/upgradables")
async def collect_upgradables():
    cfg = await _get_cfg()
    if not cfg.get("sddc_host"):
        raise HTTPException(status_code=503, detail="SDDC Manager not configured")
    try:
        domains_data = await _sddc_get(cfg, "/v1/domains")
        upgradables = []
        for d in domains_data.get("elements", []):
            domain_id = d.get("id", "")
            if not domain_id:
                continue
            try:
                u = await _sddc_get(cfg, f"/v1/upgradables/domains/{domain_id}")
                for item in u.get("elements", []):
                    upgradables.append({
                        "domain_id": domain_id,
                        "domain_name": d.get("name", ""),
                        "component_type": item.get("componentType", ""),
                        "current_version": item.get("currentVersion", ""),
                        "target_version": item.get("targetVersion", ""),
                        "upgradable": item.get("upgradable", False),
                        "blocking_reasons": item.get("blockingReasons", []),
                    })
            except Exception as e:
                logger.warning(f"Could not fetch upgradables for domain {domain_id}: {e}")
        return {"upgradables": upgradables, "timestamp": datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"SDDC Manager API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/management-plane")
async def collect_management_plane():
    """
    Collects management plane component inventory from SDDC Manager:
    vCenter, NSX Manager (cluster + nodes), SDDC Manager itself, and VCF internal services.
    """
    cfg = await _get_cfg()
    if not cfg.get("sddc_host"):
        raise HTTPException(status_code=503, detail="SDDC Manager not configured")
    try:
        async with httpx.AsyncClient(verify=cfg.get("sddc_verify_ssl", False), timeout=20.0) as client:
            token = await _get_token(cfg)
            host = cfg["sddc_host"]
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

            async def _get(path: str):
                try:
                    r = await client.get(f"https://{host}{path}", headers=headers)
                    if r.status_code == 401:
                        global _sddc_token
                        _sddc_token = None
                        t2 = await _get_token(cfg)
                        r = await client.get(f"https://{host}{path}",
                                             headers={"Authorization": f"Bearer {t2}", "Accept": "application/json"})
                    return r.json() if r.status_code == 200 else {}
                except Exception:
                    return {}

            sddc_raw, vc_raw, nsx_raw, svc_raw = await asyncio.gather(
                _get("/v1/sddc-managers"),
                _get("/v1/vcenters"),
                _get("/v1/nsxt-clusters"),
                _get("/v1/vcf-services"),
            )

        sddc_manager = [
            {"name": e.get("fqdn", ""), "fqdn": e.get("fqdn", ""), "version": e.get("version", ""),
             "status": "ACTIVE", "ip": e.get("ipAddress", "")}
            for e in sddc_raw.get("elements", [])
        ]

        vcenter = [
            {"name": e.get("fqdn", ""), "fqdn": e.get("fqdn", ""), "version": e.get("version", ""),
             "status": e.get("status", ""), "id": e.get("id", "")}
            for e in vc_raw.get("elements", [])
        ]

        # Flatten NSX cluster nodes into individual manager entries
        nsx_manager = []
        for cluster in nsx_raw.get("elements", []):
            cluster_version = cluster.get("version", "")
            cluster_status = cluster.get("status", "")
            vip_fqdn = cluster.get("vipFqdn", "")
            nodes = cluster.get("nodes", [])
            if nodes:
                for node in nodes:
                    nsx_manager.append({
                        "name": node.get("fqdn", node.get("name", "")),
                        "fqdn": node.get("fqdn", ""),
                        "version": cluster_version,
                        "status": cluster_status,
                        "cluster_vip": vip_fqdn,
                    })
            else:
                nsx_manager.append({
                    "name": vip_fqdn, "fqdn": vip_fqdn,
                    "version": cluster_version, "status": cluster_status,
                })

        # Internal VCF services running within SDDC Manager
        SERVICE_LABELS = {
            "COMMON_SERVICES": "Common Services",
            "DOMAIN_MANAGER": "Domain Manager",
            "LCM": "Lifecycle Manager",
            "OPERATIONS_MANAGER": "Operations Manager",
            "SDDC_MANAGER_UI": "SDDC Manager UI",
        }
        vcf_services = [
            {
                "name": SERVICE_LABELS.get(e.get("name", ""), e.get("name", "")),
                "fqdn": cfg["sddc_host"],
                "version": e.get("version", ""),
                "status": e.get("status", ""),
            }
            for e in svc_raw.get("elements", [])
        ]

        result = {
            "sddc_manager": sddc_manager,
            "vcenter": vcenter,
            "nsx_manager": nsx_manager,
            "vcf_management_services": vcf_services,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # ── Error context enrichment ──────────────────────────────────────────
        # A bare red "ERROR" badge is useless to the operator. When any
        # component reports a non-healthy status, pull SDDC Manager's task log
        # once and attach WHERE the status comes from and the most likely WHY,
        # so the UI can explain instead of just alarming.
        _HEALTHY = {"", "ACTIVE", "UP", "RUNNING", "CONNECTED", "GREEN", "SUCCESSFUL"}
        all_entries = [e for group in ("sddc_manager", "vcenter", "nsx_manager",
                                       "vcf_management_services") for e in result[group]]
        if any((e.get("status") or "").upper() not in _HEALTHY for e in all_entries):
            async with httpx.AsyncClient(verify=cfg.get("sddc_verify_ssl", False), timeout=15.0) as client:
                headers = {"Authorization": f"Bearer {await _get_token(cfg)}", "Accept": "application/json"}
                try:
                    r = await client.get(f"https://{cfg['sddc_host']}/v1/tasks?limit=30", headers=headers)
                    tasks = r.json().get("elements", []) if r.status_code == 200 else []
                except Exception:
                    tasks = []
            failed = [t for t in tasks if (t.get("status") or "").upper() == "FAILED"]

            def _fmt(t: dict) -> str:
                ts = (t.get("creationTimestamp") or "")[:10]
                return f"'{t.get('name', 'unknown task')}' ({ts})"

            for e in all_entries:
                status = (e.get("status") or "").upper()
                if status in _HEALTHY:
                    continue
                related = [t for t in failed if any(
                    r.get("resourceId") == e.get("id") or
                    (e.get("fqdn") and e["fqdn"] in str(r.get("fqdn", "")))
                    for r in (t.get("resources") or []))]
                e["status_source"] = "SDDC Manager resource inventory"
                if related:
                    e["status_detail"] = (
                        f"SDDC Manager records this resource as {status}. The appliance itself may be "
                        f"healthy — this is SDDC Manager's inventory state, often left behind by a failed "
                        f"operation. Related failed task: {', '.join(_fmt(t) for t in related[:2])}. "
                        f"Open SDDC Manager → Tasks to retry or acknowledge it."
                    )
                elif failed:
                    e["status_detail"] = (
                        f"SDDC Manager records this resource as {status}. The appliance itself may be "
                        f"healthy — this is SDDC Manager's inventory state. No failed task is directly "
                        f"linked to this resource, but recent failures exist: "
                        f"{', '.join(_fmt(t) for t in failed[:2])}. Check SDDC Manager → Tasks."
                    )
                else:
                    e["status_detail"] = (
                        f"SDDC Manager records this resource as {status}. The appliance itself may be "
                        f"healthy — this is SDDC Manager's inventory state. No failed tasks found; "
                        f"check the resource in SDDC Manager → Inventory."
                    )

        return result
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"SDDC Manager API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/collect/upgrade-sequence")
async def collect_upgrade_sequence():
    """
    Returns domain ordering + per-domain component upgrade sequence.
    VCF rule: management domain first, then workload domains ordered by health.
    Within a domain: NSX → vCenter/SDDC Manager → ESXi hosts → management tools.
    """
    cfg = await _get_cfg()
    if not cfg.get("sddc_host"):
        raise HTTPException(status_code=503, detail="SDDC Manager not configured")
    try:
        domains_data = await _sddc_get(cfg, "/v1/domains")
        domains = domains_data.get("elements", [])

        # Separate management from workload domains
        mgmt_domains = [d for d in domains if d.get("type", "").upper() == "MANAGEMENT"]
        workload_domains = [d for d in domains if d.get("type", "").upper() != "MANAGEMENT"]

        async def _domain_upgradables(domain_id: str) -> list:
            try:
                u = await _sddc_get(cfg, f"/v1/upgradables/domains/{domain_id}")
                return u.get("elements", [])
            except Exception:
                return []

        # Fetch upgradables for all domains in parallel
        import asyncio
        all_domain_ids = [d.get("id") for d in domains if d.get("id")]
        all_upgradables = await asyncio.gather(*[_domain_upgradables(did) for did in all_domain_ids])
        upgradables_by_domain = {did: items for did, items in zip(all_domain_ids, all_upgradables)}

        # Component ordering within a domain (lower = upgrade first)
        _COMPONENT_ORDER = {
            "NSX": 1, "NSX_T": 1, "NSX_MANAGER": 1,
            "VCENTER": 2, "SDDC_MANAGER": 2,
            "ESXI": 3, "HOST": 3,
            "VROPS": 4, "VROPS_CLOUD": 4, "VCF_OPERATIONS": 4,
            "VRLI": 5, "VCF_LOGS": 5,
        }

        def _order_components(items: list) -> list:
            def _key(item):
                ctype = item.get("componentType", "").upper()
                for k, v in _COMPONENT_ORDER.items():
                    if k in ctype:
                        return v
                return 99
            return sorted(items, key=_key)

        def _build_domain_step(domain: dict, step_index: int) -> dict:
            did = domain.get("id", "")
            items = upgradables_by_domain.get(did, [])
            actionable = [i for i in items if i.get("upgradable", False)]
            blocked = [i for i in items if not i.get("upgradable", True) and i.get("componentType")]
            ordered = _order_components(actionable)
            return {
                "step": step_index,
                "domain_id": did,
                "domain_name": domain.get("name", ""),
                "domain_type": domain.get("type", ""),
                "status": domain.get("status", ""),
                "components_to_upgrade": [
                    {
                        "order": idx + 1,
                        "component_type": c.get("componentType", ""),
                        "current_version": c.get("currentVersion", ""),
                        "target_version": c.get("targetVersion", ""),
                        "blocking_reasons": c.get("blockingReasons", []),
                    }
                    for idx, c in enumerate(ordered)
                ],
                "blockers": [
                    {
                        "component_type": b.get("componentType", ""),
                        "current_version": b.get("currentVersion", ""),
                        "blocking_reasons": b.get("blockingReasons", []),
                    }
                    for b in blocked
                ],
                "has_blockers": len(blocked) > 0,
                "upgradable_count": len(actionable),
                "blocked_count": len(blocked),
            }

        steps = []
        step = 1
        for d in mgmt_domains:
            steps.append(_build_domain_step(d, step))
            step += 1
        # Sort workload domains: healthy ones first, then degraded
        workload_domains.sort(key=lambda d: 0 if d.get("status", "").upper() == "ACTIVE" else 1)
        for d in workload_domains:
            steps.append(_build_domain_step(d, step))
            step += 1

        total_blockers = sum(s["blocked_count"] for s in steps)
        total_upgradable = sum(s["upgradable_count"] for s in steps)
        safe_to_proceed = total_blockers == 0 and total_upgradable > 0

        return {
            "steps": steps,
            "total_domains": len(steps),
            "total_upgradable": total_upgradable,
            "total_blockers": total_blockers,
            "safe_to_proceed": safe_to_proceed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"SDDC Manager API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    cfg = await _get_cfg()
    return {
        "status": "healthy",
        "service": "collector-sddc",
        "sddc_configured": bool(cfg.get("sddc_host")),
    }
