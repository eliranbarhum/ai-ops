import os
import re
import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("tools.env_manifest")

COLLECTOR_VCENTER_URL = os.getenv("COLLECTOR_VCENTER_URL", "http://collector-vcenter:8003")
COLLECTOR_VROPS_URL   = os.getenv("COLLECTOR_VROPS_URL",   "http://collector-vrops:8004")
MANIFEST_PATH = "/tmp/env-manifest.yaml"


def _detect_pattern(names: list[str]) -> Optional[str]:
    """Return a ^prefix regex if names share a common prefix of ≥3 chars, else None."""
    if len(names) < 2:
        return None
    prefix = names[0]
    for n in names[1:]:
        while not n.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            break
    return f"^{re.escape(prefix)}" if len(prefix) >= 3 else None


def _coerce_version(raw: str) -> str:
    """Normalise version string: strip build suffix, return X.Y.Z or 'unknown'."""
    if not raw:
        return "unknown"
    # "9.0.2-25148076" → "9.0.2"
    return raw.split("-")[0].strip() or "unknown"


async def _get_vrops_component_versions() -> dict:
    """Return {'vcenter': '9.0.2', ...} from vROps /collect/component-versions."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{COLLECTOR_VROPS_URL}/collect/component-versions")
            r.raise_for_status()
            data = r.json().get("versions", {})
            result = {}
            for key, items in data.items():
                if items:
                    result[key] = _coerce_version(items[0].get("version", ""))
            return result
    except Exception as e:
        logger.warning(f"Could not fetch component versions from vROps: {e}")
        return {}


async def _get_vrops_host_details() -> list[dict]:
    """Return per-host data from vROps /collect/host-details."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(f"{COLLECTOR_VROPS_URL}/collect/host-details")
            r.raise_for_status()
            return r.json().get("hosts", [])
    except Exception as e:
        logger.warning(f"Could not fetch host details from vROps: {e}")
        return []


async def get_env_manifest() -> dict:
    from tools.vcenter_inventory import get_vcenter_inventory
    from tools.cluster_capacity import get_cluster_capacity

    inventory, _capacity, comp_versions, vrops_hosts = await asyncio.gather(
        get_vcenter_inventory(),
        get_cluster_capacity(),
        _get_vrops_component_versions(),
        _get_vrops_host_details(),
        return_exceptions=True,
    )

    # --- inventory ---
    inv_entities = []
    inv_raw = {}
    if not isinstance(inventory, Exception):
        inv_entities = inventory.get("normalized", {}).get("entities", [])
        inv_raw = inventory.get("raw", {})
    else:
        logger.warning(f"get_vcenter_inventory failed: {inventory}")

    clusters_meta = [e for e in inv_entities if e.get("entity_type") == "cluster"]
    datacenters = [e["name"] for e in inv_entities if e.get("entity_type") == "datacenter"]
    vm_count = inv_raw.get("vm_count", 0)

    # --- component versions from vROps ---
    comp = comp_versions if isinstance(comp_versions, dict) else {}
    vcenter_version = comp.get("vcenter", "unknown")

    # --- host details from vROps ---
    raw_hosts: list[dict] = vrops_hosts if isinstance(vrops_hosts, list) else []

    hosts_all = []
    for h in raw_hosts:
        esxi_ver = _coerce_version(h.get("esxi_version", ""))
        hosts_all.append({
            "name": h["name"],
            "version": esxi_ver,
            "cpu_cores": h.get("cpu_cores", 0),
            "memory_gb": h.get("memory_gb", 0),
        })

    # vCenter version cannot be less than ESXi version — use ESXi as lower bound
    # when vCenter version is still unknown.
    if vcenter_version == "unknown" and hosts_all:
        esxi_versions = [h["version"] for h in hosts_all if h["version"] != "unknown"]
        if esxi_versions:
            vcenter_version = max(esxi_versions)
            logger.info(f"vCenter version derived from ESXi (lower bound): {vcenter_version}")

    # Fallback for names: inventory raw hosts when vROps host-details is empty
    if not hosts_all:
        for h in inv_raw.get("hosts", []):
            hosts_all.append({
                "name": h["name"],
                "version": "unknown",
                "cpu_cores": 0,
                "memory_gb": 0,
            })

    # --- cluster → host assignment ---
    # The inventory API returns hosts and clusters as separate flat lists with no
    # cluster membership field per host. Single-cluster envs get all hosts assigned;
    # multi-cluster envs distribute hosts sequentially by declared host_count.
    clusters_out = []
    if len(clusters_meta) == 1:
        c = clusters_meta[0]
        clusters_out.append({
            "name": c["name"],
            "host_count": c.get("host_count") or len(hosts_all),
            "ha_enabled": bool(c.get("ha_enabled", False)),
            "drs_enabled": bool(c.get("drs_enabled", False)),
            "vsan_enabled": False,
            "hosts": hosts_all,
        })
    else:
        if clusters_meta:
            logger.warning(f"Multiple clusters ({len(clusters_meta)}) — host-to-cluster assignment is approximate")
        idx = 0
        for c in clusters_meta:
            n = c.get("host_count", 0)
            slice_ = hosts_all[idx: idx + n]
            idx += n
            clusters_out.append({
                "name": c["name"],
                "host_count": c.get("host_count") or len(slice_),
                "ha_enabled": bool(c.get("ha_enabled", False)),
                "drs_enabled": bool(c.get("drs_enabled", False)),
                "vsan_enabled": False,
                "hosts": slice_,
            })

    # --- naming patterns ---
    # VM, datastore, and portgroup names are not returned by any of the three
    # source tools; patterns remain null.
    host_names = [h["name"] for h in hosts_all]
    logger.info(f"Host naming pattern: {_detect_pattern(host_names)}")

    manifest = {
        "vcenter_version": vcenter_version,
        "clusters": clusters_out,
        "datacenters": datacenters,
        "naming_patterns": {
            "vm": None,
            "datastore": None,
            "portgroup": None,
        },
        "vm_count": vm_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Side effect: write to /tmp/env-manifest.yaml
    # JSON is a valid YAML subset — no pyyaml dependency required.
    try:
        with open(MANIFEST_PATH, "w") as f:
            f.write("---\n")
            f.write(json.dumps(manifest, indent=2, default=str))
            f.write("\n")
        logger.info(f"Manifest written to {MANIFEST_PATH}")
    except Exception as e:
        logger.warning(f"Could not write manifest to {MANIFEST_PATH}: {e}")

    return manifest
