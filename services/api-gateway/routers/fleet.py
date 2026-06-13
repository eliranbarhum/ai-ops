import os
import json as _json
import time as _time
import httpx
from fastapi import APIRouter
from shared import (
    CONFIG_STORE_URL, VCENTER_COLLECTOR, VROPS_COLLECTOR,
    SCORING_ENGINE_URL, _get_redis, _proxy,
)
from data_router import build_fleet as _build_fleet

router = APIRouter()

_fleet_cache: dict = {"data": None, "ts": 0.0}
_FLEET_CACHE_TTL = 60
_FLEET_REDIS_KEY = "fleet:cache"


@router.get("/api/v1/fleet")
async def fleet():
    r = await _get_redis()

    if r:
        try:
            cached = await r.get(_FLEET_REDIS_KEY)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    now = _time.time()
    if _fleet_cache["data"] is not None and (now - _fleet_cache["ts"]) < _FLEET_CACHE_TTL:
        return _fleet_cache["data"]

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            cfg_resp = await client.get(f"{CONFIG_STORE_URL}/config/raw")
            cfg = cfg_resp.json() if cfg_resp.status_code == 200 else {}
        except Exception:
            cfg = {}

    result = await _build_fleet(
        vcenter_url=VCENTER_COLLECTOR,
        vrops_url=VROPS_COLLECTOR,
        sddc_url=os.getenv("SDDC_COLLECTOR_URL", "http://collector-sddc:8005"),
        cfg=cfg,
    )

    if r:
        try:
            await r.set(_FLEET_REDIS_KEY, _json.dumps(result), ex=_FLEET_CACHE_TTL)
        except Exception:
            pass

    _fleet_cache["data"] = result
    _fleet_cache["ts"] = _time.time()
    return result


@router.get("/api/v1/fleet/hosts/{host_id}")
async def fleet_host_detail(host_id: str):
    return await _proxy("GET", f"{VCENTER_COLLECTOR}/collect/host/{host_id}", timeout=30.0)


@router.get("/api/v1/fleet/networks/{network_id}")
async def fleet_network_detail(network_id: str):
    return await _proxy("GET", f"{VCENTER_COLLECTOR}/collect/network/{network_id}", timeout=20.0)


@router.get("/api/v1/scoring/history")
async def scoring_history(limit: int = 100):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{SCORING_ENGINE_URL}/history", params={"limit": limit})
        r.raise_for_status()
        return r.json()


_SDDC_COLLECTOR = os.getenv("SDDC_COLLECTOR_URL", "http://collector-sddc:8005")


def _rollback_risk(
    upgradables: list, hosts: list, clusters: list,
    vm_count: int = 0, vsan_enabled: bool = False,
) -> dict:
    """Heuristic rollback risk score (0–100) for a proposed upgrade batch."""
    score = 0
    reasons = []

    if len(upgradables) > 3:
        score += 20
        reasons.append(f"{len(upgradables)} components to upgrade simultaneously")

    blocker_count = sum(1 for u in upgradables if not u.get("upgradable", True))
    if blocker_count:
        score += 30
        reasons.append(f"{blocker_count} upgrade blocker{'s' if blocker_count != 1 else ''} present")

    host_count = len(hosts)
    if host_count > 10:
        score += 15
        reasons.append(f"{host_count} ESXi hosts require reboots")
    elif host_count > 4:
        score += 8
        reasons.append(f"{host_count} ESXi hosts require reboots")

    if vm_count:
        score += min(vm_count, 15)
        reasons.append(f"{vm_count} powered-on VM{'s' if vm_count != 1 else ''} may need vMotion")

    if vsan_enabled:
        score += 10
        reasons.append("vSAN datastores require extra care during host reboots")

    degraded_clusters = [c for c in clusters if c.get("status", "").upper() not in ("ACTIVE", "")]
    if degraded_clusters:
        score += 25
        reasons.append(f"{len(degraded_clusters)} cluster(s) already degraded")

    score = min(score, 100)
    risk_level = "high" if score >= 70 else "medium" if score >= 40 else "low"

    return {
        "score": score,
        "level": risk_level,
        "reasons": reasons,
        "host_count": host_count,
        "vm_count": vm_count,
        "vsan_enabled": vsan_enabled,
    }


@router.get("/api/v1/fleet/bundles")
async def fleet_bundles():
    """Patch Bundle Advisor — upgradable components with rollback risk scoring."""
    import asyncio

    async with httpx.AsyncClient(timeout=20.0) as client:
        async def _get_sddc(path: str) -> dict:
            try:
                r = await client.get(f"{_SDDC_COLLECTOR}{path}")
                return r.json() if r.status_code == 200 else {}
            except Exception:
                return {}

        async def _get_vcenter(path: str) -> dict:
            try:
                r = await client.get(f"{VCENTER_COLLECTOR}{path}")
                return r.json() if r.status_code == 200 else {}
            except Exception:
                return {}

        upgradables_raw, hosts_raw, clusters_raw, system, vcenter_inv = await asyncio.gather(
            _get_sddc("/collect/upgradables"),
            _get_sddc("/collect/hosts"),
            _get_sddc("/collect/clusters"),
            _get_sddc("/collect/system"),
            _get_vcenter("/collect/inventory"),
        )

    upgradables = upgradables_raw.get("upgradables", [])
    hosts = hosts_raw.get("hosts", [])
    clusters = clusters_raw.get("clusters", [])
    sddc_version = system.get("version", "unknown")
    vm_count = vcenter_inv.get("vm_count", 0)
    vsan_enabled = any(c.get("vsan_enabled") for c in clusters)

    actionable = [u for u in upgradables if u.get("upgradable", False)]
    blocked = [u for u in upgradables if not u.get("upgradable", True) and u.get("component_type")]

    risk = _rollback_risk(actionable, hosts, clusters, vm_count=vm_count, vsan_enabled=vsan_enabled)

    recommendations = []
    if blocked:
        recommendations.append(f"Resolve {len(blocked)} upgrade blocker(s) before proceeding")
    if risk["level"] == "high":
        recommendations.append("Schedule upgrade during a maintenance window — risk is high")
    elif risk["level"] == "medium":
        recommendations.append("Verify cluster health before upgrading")
    if actionable:
        first = actionable[0]
        recommendations.append(
            f"Recommended next: upgrade {first.get('component_type', 'component')} "
            f"from {first.get('current_version', '?')} to {first.get('target_version', '?')}"
        )

    return {
        "sddc_version": sddc_version,
        "upgradable_components": actionable,
        "blocked_components": blocked,
        "host_count": len(hosts),
        "cluster_count": len(clusters),
        "vm_count": vm_count,
        "vsan_enabled": vsan_enabled,
        "rollback_risk": risk,
        "recommendations": recommendations,
    }



@router.get("/api/v1/fleet/score-diff")
async def fleet_score_diff():
    """Proxy the scoring-engine diff endpoint for the Fleet page banner."""
    return await _proxy("GET", f"{SCORING_ENGINE_URL}/diff")
