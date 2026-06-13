"""
DataSourceRouter — structured collection with priority and fallback rules.

Priority per field:
  host hardware (cpu_cores, memory_gb, cpu_model)  → vROps  > vCenter host-hardware
  host usage % (cpu_usage, ram_usage)              → vROps  > vCenter host-hardware
  esxi_version                                     → vROps  > SDDC /v1/hosts
  cluster cpu%/ram%                                → vROps  (no fallback)
  datastores                                       → vCenter > vROps
  networks                                         → vCenter > vROps
  vm_count                                         → vCenter (no fallback)
  management_plane versions                        → vROps  > SDDC

All sources are always attempted in parallel. Fallback is applied during merge,
not by skipping — so partial results are always preferred over empty results.
"""

import asyncio
import logging
import time
import httpx

logger = logging.getLogger("data-router")


async def _safe(client: httpx.AsyncClient, url: str, timeout: float = 8.0) -> dict:
    try:
        r = await asyncio.wait_for(client.get(url), timeout=timeout)
        return r.json() if r.status_code == 200 else {}
    except Exception as exc:
        logger.warning(f"data_router: {url} failed — {exc}")
        return {}


async def build_fleet(
    vcenter_url: str,
    vrops_url: str,
    sddc_url: str,
    cfg: dict,
) -> dict:
    has_vcenter = bool(cfg.get("vcenter_host"))
    has_vrops   = bool(cfg.get("vrops_host"))
    has_sddc    = bool(cfg.get("sddc_host"))

    async with httpx.AsyncClient(timeout=35.0) as client:
        (
            vc_inventory,
            vc_hosts,
            vc_host_hw,       # cpu/ram from vCenter namespace-management (VCF 9.x fallback)
            vc_datastores,
            vc_networks,
            vc_versions,
            vc_cluster,       # cluster cpu/ram % from vCenter namespace-management
            vrops_cluster,
            vrops_versions,
            vrops_host_hw,    # cpu_cores, memory_gb, cpu_model, usage from vROps
            vrops_datastores, # fallback datastores
            vrops_networks,   # fallback networks (DVS portgroups)
            sddc_hosts,       # esxi_version fallback
            sddc_mgmt_plane,  # NSX, vCenter, SDDC Manager, VCF services from SDDC API
        ) = await asyncio.gather(
            _safe(client, f"{vcenter_url}/collect/inventory"),
            _safe(client, f"{vcenter_url}/collect/hosts"),
            _safe(client, f"{vcenter_url}/collect/host-hardware"),
            _safe(client, f"{vcenter_url}/collect/datastores"),
            _safe(client, f"{vcenter_url}/collect/networks"),
            _safe(client, f"{vcenter_url}/collect/versions"),
            _safe(client, f"{vcenter_url}/collect/cluster-metrics"),
            _safe(client, f"{vrops_url}/collect/cluster-metrics"),
            _safe(client, f"{vrops_url}/collect/component-versions", timeout=15.0),
            _safe(client, f"{vrops_url}/collect/host-details", timeout=30.0),
            _safe(client, f"{vrops_url}/collect/datastores", timeout=20.0),
            _safe(client, f"{vrops_url}/collect/networks"),
            _safe(client, f"{sddc_url}/collect/hosts"),
            _safe(client, f"{sddc_url}/collect/management-plane", timeout=20.0),
        )

    # ── Datastores: vCenter primary, vROps fallback ───────────────────────────
    vc_ds_list    = vc_datastores.get("datastores", [])
    vrops_ds_list = vrops_datastores.get("datastores", [])
    datastores    = vc_ds_list if vc_ds_list else vrops_ds_list

    # ── Networks: vCenter primary, vROps (DVS portgroups) fallback ────────────
    vc_net_list    = vc_networks.get("networks", [])
    vrops_net_list = vrops_networks.get("networks", [])
    networks       = vc_net_list if vc_net_list else vrops_net_list

    # ── Cluster metrics ───────────────────────────────────────────────────────
    ds_list       = datastores
    total_cap_gb  = sum(d.get("capacity_gb", 0) for d in ds_list)
    total_free_gb = sum(d.get("free_gb", 0) for d in ds_list)
    total_used_gb = round(total_cap_gb - total_free_gb, 1)

    # vROps primary; vCenter namespace-management fallback
    vrops_usage_by_name = {r["name"]: r for r in vrops_cluster.get("resources", [])}
    vc_usage_by_name    = {r["name"]: r for r in vc_cluster.get("resources", [])}

    clusters = []
    for cl in vc_inventory.get("clusters", []):
        name  = cl.get("name", "")
        usage = vrops_usage_by_name.get(name) or vc_usage_by_name.get(name) or {}
        clusters.append({
            "name":                    name,
            "cluster":                 cl.get("cluster", ""),
            "ha_enabled":              cl.get("ha_enabled", False),
            "drs_enabled":             cl.get("drs_enabled", False),
            "cpu_usage_pct":           usage.get("cpu_usage", 0),
            "ram_usage_pct":           usage.get("ram_usage", 0),
            "storage_capacity_gb":     total_cap_gb,
            "storage_provisioned_gb":  total_used_gb,
        })

    # ── Host hardware merge ───────────────────────────────────────────────────
    # Build lookup maps: name (lower) → data
    vrops_hw_map = {}
    for h in vrops_host_hw.get("hosts", []):
        n = h.get("name", "")
        vrops_hw_map[n.lower()] = h
        vrops_hw_map[n.split(".")[0].lower()] = h

    vc_hw_map = {}
    for h in vc_host_hw.get("hosts", []):
        n = h.get("name", "")
        vc_hw_map[n.lower()] = h
        vc_hw_map[n.split(".")[0].lower()] = h

    sddc_hw_map = {}
    for h in sddc_hosts.get("hosts", []):
        n = h.get("fqdn", "") or h.get("hostname", "") or h.get("name", "")
        sddc_hw_map[n.lower()] = h
        sddc_hw_map[n.split(".")[0].lower()] = h

    merged_hosts = []
    for h in vc_hosts.get("hosts", []):
        name  = h.get("name", "")
        key   = name.lower()
        short = name.split(".")[0].lower()

        vrops = vrops_hw_map.get(key) or vrops_hw_map.get(short) or {}
        vc_hw = vc_hw_map.get(key)   or vc_hw_map.get(short)   or {}
        sddc  = sddc_hw_map.get(key) or sddc_hw_map.get(short) or {}

        # Hardware: vROps > vCenter detail > zeros
        cpu_cores  = vrops.get("cpu_cores")  or vc_hw.get("cpu_cores")  or 0
        memory_gb  = vrops.get("memory_gb")  or vc_hw.get("memory_gb")  or 0
        cpu_model  = vrops.get("cpu_model")  or vc_hw.get("cpu_model")  or ""
        cpu_sockets= vrops.get("cpu_sockets") or 0
        cpu_threads= vrops.get("cpu_threads") or 0

        # Usage %: vROps > vCenter quickStats > 0
        cpu_usage  = vrops.get("cpu_usage")  or vc_hw.get("cpu_usage")  or 0.0
        ram_usage  = vrops.get("ram_usage")  or vc_hw.get("ram_usage")  or 0.0

        # ESXi version: vROps > SDDC
        esxi_version = vrops.get("esxi_version") or sddc.get("esxi_version") or ""

        merged_hosts.append({
            **h,
            "cpu_model":    cpu_model,
            "cpu_sockets":  cpu_sockets,
            "cpu_cores":    cpu_cores,
            "cpu_threads":  cpu_threads,
            "memory_gb":    memory_gb,
            "cpu_usage":    cpu_usage,
            "ram_usage":    ram_usage,
            "esxi_version": esxi_version,
        })

    # ── Management plane ──────────────────────────────────────────────────────
    # Priority: vROps (has build numbers) > SDDC Manager API > vCenter collector fallback
    mp_raw = vrops_versions.get("versions", {})

    # Fill gaps from SDDC Manager API (has FQDN, version, status for all components)
    for key in ("sddc_manager", "nsx_manager", "vcenter", "vcf_management_services"):
        sddc_items = sddc_mgmt_plane.get(key, [])
        if sddc_items and not mp_raw.get(key):
            mp_raw[key] = sddc_items

    # Also merge SDDC nsx/sddc data into existing vROps entries if vROps returned empty
    if not mp_raw.get("nsx_manager") and sddc_mgmt_plane.get("nsx_manager"):
        mp_raw["nsx_manager"] = sddc_mgmt_plane["nsx_manager"]
    if not mp_raw.get("sddc_manager") and sddc_mgmt_plane.get("sddc_manager"):
        mp_raw["sddc_manager"] = sddc_mgmt_plane["sddc_manager"]

    # Carry SDDC Manager's status + error context into entries that came from
    # vROps (which has versions/builds but no resource state) — matched by FQDN.
    for key in ("vcenter", "nsx_manager", "sddc_manager", "vcf_management_services"):
        sddc_by_fqdn = {
            (i.get("fqdn") or i.get("name")): i
            for i in sddc_mgmt_plane.get(key, []) if i.get("fqdn") or i.get("name")
        }
        for entry in mp_raw.get(key, []) or []:
            src = sddc_by_fqdn.get(entry.get("fqdn") or entry.get("name"))
            if not src:
                continue
            for field in ("status", "status_detail", "status_source", "id", "ip"):
                if src.get(field) and not entry.get(field):
                    entry[field] = src[field]

    # Last resort: populate vcenter from vCenter collector if still missing
    if not mp_raw.get("vcenter"):
        vc_fqdn = vc_versions.get("vcenter_fqdn", "")
        vc_ver  = (vc_versions.get("versions") or {}).get("vcenter", "")
        if vc_fqdn:
            mp_raw["vcenter"] = [{"name": vc_fqdn, "fqdn": vc_fqdn, "version": vc_ver, "build": ""}]

    # ── Sources metadata (shown in Fleet UI) ──────────────────────────────────
    vc_hw_hosts = [h for h in vc_host_hw.get("hosts", []) if h.get("memory_gb", 0) > 0]
    sources = {
        "vcenter":  has_vcenter and bool(vc_hosts.get("hosts")),
        "vrops":    has_vrops   and bool(vrops_host_hw.get("hosts")),
        "sddc":     has_sddc    and bool(sddc_hosts.get("hosts")),
        "datastores_source":    "vcenter" if vc_ds_list else ("vrops" if vrops_ds_list else "none"),
        "networks_source":      "vcenter" if vc_net_list else ("vrops" if vrops_net_list else "none"),
        "hardware_source":      "vrops"   if vrops_host_hw.get("hosts") else ("vcenter-ns" if vc_hw_hosts else "none"),
        "usage_source":         "vrops"   if vrops_host_hw.get("hosts") else ("vcenter-ns" if vc_cluster.get("resources") else "none"),
        "cluster_usage_source": "vrops"   if vrops_cluster.get("resources") else ("vcenter-ns" if vc_cluster.get("resources") else "none"),
        "vrops_error":          not bool(vrops_host_hw.get("hosts")) and has_vrops,
    }

    return {
        "datacenters":       vc_inventory.get("datacenters", []),
        "clusters":          clusters,
        "hosts":             merged_hosts,
        "vm_count":          vc_inventory.get("vm_count", 0),
        "datastores":        datastores,
        "networks":          networks,
        "management_plane":  mp_raw,
        "component_versions":vc_versions.get("versions", {}),
        "_sources":          sources,
    }
