"""
Broadcom interoperability and hardware HCL checker.

Checks three things:
  1. Component version interoperability — are all running component versions
     mutually supported per the vSphere Interoperability Matrix?
  2. Upgrade path validity — is there a supported direct path from the current
     SDDC Manager version to the target VCF version?
  3. Hardware HCL — are the server CPU platforms certified for the target ESXi version?

Data source: static embedded JSON (vcf_interop.json) derived from Broadcom
release notes and the VMware Compatibility Guide. Covers VCF 5.x and 9.0.
"""

import json
import logging
import os
import re
from pathlib import Path
import httpx

logger = logging.getLogger("tool.broadcom_interop")

VROPS_COLLECTOR_URL = os.getenv("VROPS_COLLECTOR_URL", "http://collector-vrops:8004")
VCENTER_COLLECTOR_URL = os.getenv("VCENTER_COLLECTOR_URL", "http://collector-vcenter:8003")
CONFIG_STORE_URL = os.getenv("CONFIG_STORE_URL", "http://config-store:8009")

_DATA_FILE = Path(__file__).parent.parent / "data" / "vcf_interop.json"
_interop_data: dict | None = None


def _load_data() -> dict:
    global _interop_data
    if _interop_data is None:
        try:
            _interop_data = json.loads(_DATA_FILE.read_text())
        except Exception as e:
            logger.error(f"Cannot load vcf_interop.json: {e}")
            _interop_data = {}
    return _interop_data


def _ver_major(ver: str) -> str:
    parts = ver.replace("-", ".").split(".")
    return parts[0] if parts else ""


def _ver_tuple(ver: str) -> tuple:
    try:
        return tuple(int(x) for x in ver.replace("-", ".").split(".") if x.isdigit())
    except Exception:
        return (0,)


def _detect_platform(cpu_model: str) -> str:
    """Map a CPU model string to a hardware platform key used in vcf_interop.json."""
    upper = cpu_model.upper()

    # Intel Xeon 6 (new branding post-2024, Granite Rapids / Sierra Forest)
    if re.search(r'\bXEON\s+6\b', upper):
        return "intel_xeon_6"

    # Intel Xeon Scalable — identified by 4-digit model number after tier name
    m = re.search(r'(?:PLATINUM|GOLD|SILVER|BRONZE)\s+(\d{4})', upper)
    if m:
        n = int(m.group(1))
        decade = n // 100
        if decade >= 85 or decade >= 65:
            return "intel_xeon_5th_gen"
        if decade in (84, 64, 54, 44, 34):
            return "intel_xeon_4th_gen"
        if decade in (83, 63, 53, 43, 33):
            return "intel_xeon_3rd_gen"
        if decade in (82, 62, 52, 42, 32):
            return "intel_xeon_2nd_gen"
        return "intel_xeon_1st_gen"

    # AMD EPYC
    m = re.search(r'EPYC\s+(\d{4})', upper)
    if m:
        n = int(m.group(1))
        if n >= 9000:  return "amd_epyc_genoa"
        if n >= 7003:  return "amd_epyc_milan"
        if n >= 7002:  return "amd_epyc_rome"
        return "amd_epyc_naples"

    # Intel server board model numbers → approximate CPU generation
    # S2600 family = Skylake/Cascade Lake era (1st/2nd gen Xeon Scalable)
    if re.search(r'\bS26[0-9]{2}', upper):
        return "intel_xeon_2nd_gen"
    # R4xx/R5xx = Dell/HPE generation hints
    if re.search(r'\bR[45][0-9]{2}\b', upper):
        return "intel_xeon_3rd_gen"

    return "unknown"


def _check_interop_matrix(components: dict, target: str, data: dict) -> list[str]:
    gaps = []
    rules = data.get("interop_matrix", {}).get(target, {})
    if not rules:
        logger.warning(f"No interop matrix rules for VCF {target}")
        return gaps

    # Per-component major version check
    for comp, ver in components.items():
        if not ver:
            continue
        required_major = rules.get(f"{comp}_requires_major")
        if required_major and _ver_major(ver) != required_major:
            gaps.append(
                f"{comp.replace('_', ' ').title()} {ver} is not on major version {required_major}.x "
                f"required by VCF {target} interoperability matrix"
            )

    # vCenter and ESXi must share the same major version in all VCF releases
    vc = components.get("vcenter", "")
    esxi = components.get("esxi", "")
    if vc and esxi and _ver_major(vc) != _ver_major(esxi):
        gaps.append(
            f"vCenter {vc} and ESXi {esxi} are on different major versions — "
            "they must be on the same major version (e.g. both 9.x or both 8.x)"
        )

    return gaps


def _check_upgrade_path(sddc_ver: str, target: str, data: dict) -> list[str]:
    if not sddc_ver:
        return []

    current_mm = ".".join(sddc_ver.replace("-", ".").split(".")[:2])  # "9.1" from "9.1.0.0"

    # Already at or past the target version — environment meets or exceeds the target
    if _ver_tuple(sddc_ver) >= _ver_tuple(target) or current_mm == target or sddc_ver.startswith(target):
        return []

    gaps = []
    paths = data.get("upgrade_paths", {}).get(target, {})
    if not paths:
        return gaps

    supported = paths.get("supported_direct_sources", [])
    intermediate = paths.get("required_intermediate", {})

    if current_mm in supported:
        return gaps

    hop = intermediate.get(current_mm)
    if hop:
        gaps.append(
            f"VCF {current_mm} cannot upgrade directly to VCF {target} — "
            f"staged upgrade required: {current_mm} → {hop} → {target}. "
            f"Reference: {paths.get('reference', data.get('interop_reference_url', ''))}"
        )
    else:
        gaps.append(
            f"No supported upgrade path found from VCF {current_mm} to VCF {target} — "
            "consult Broadcom upgrade documentation"
        )
    return gaps


def _determine_upgrade_workflow(components: dict, fleet_data: dict, target: str, data: dict) -> dict:
    """
    Determine the mandatory upgrade workflow type based on KB440630.
    Returns workflow type, any blockers, and consolidation action items.
    """
    if target != "9.1":
        return {}

    paths = data.get("upgrade_paths", {}).get("9.1", {})
    workflow_rules = paths.get("workflow_rules", [])
    consolidations = paths.get("component_consolidations", {})
    mgmt_svc_reqs = paths.get("vcf_management_services_requirements", {})

    nsx_ver = components.get("nsx", "")
    nsx_major = _ver_major(nsx_ver)
    has_sddc = bool(components.get("sddc_manager", ""))

    # Detect fleet appliances from vROps fleet data
    fleet_appliances = fleet_data.get("fleet", {})
    mgmt_services = fleet_appliances.get("vcf_management_services", [])
    mgmt_service_names = [str(a.get("name", "")).lower() for a in mgmt_services]

    has_fleet_mgmt_appliance = any(
        "fleet" in n and ("management" in n or "lifecycle" in n or "lm" in n)
        for n in mgmt_service_names
    )
    has_aria_automation = any("automation" in n or "aria auto" in n for n in mgmt_service_names)
    has_vrslcm = any("lifecycle manager" in n or "vrslcm" in n or "vrlcm" in n for n in mgmt_service_names)
    has_aria_log_insight = any("log insight" in n or "aria log" in n for n in mgmt_service_names)
    has_aria_network_insight = any("network insight" in n or "aria network" in n for n in mgmt_service_names)

    # Evaluate workflow rules
    workflow_blockers = []
    workflow_notes = []
    required_workflow = "Standard component upgrade"

    condition_map = {
        "has_sddc_manager": has_sddc,
        "has_nsx_4x": nsx_major == "4",
        "has_aria_automation": has_aria_automation,
        "has_vrslcm": has_vrslcm,
        "has_aria_log_insight": has_aria_log_insight,
        "has_aria_network_insight": has_aria_network_insight,
        "vcenter_esxi_only": not has_sddc and not has_aria_automation,
    }

    for rule in workflow_rules:
        cond = rule.get("condition", "")
        if condition_map.get(cond, False):
            required_workflow = rule["workflow"]
            if rule.get("blocker"):
                workflow_blockers.append(f"[Upgrade Workflow] {rule['note']}")
            else:
                workflow_notes.append(f"[Upgrade Workflow] {rule['note']}")
            break  # First matching rule wins (ordered by severity)

    # Check component consolidations
    consolidation_actions = []
    if has_fleet_mgmt_appliance:
        c = consolidations.get("fleet_management_appliance", {})
        msg = f"[Consolidation] {c.get('note', 'Fleet Management Appliance must be decommissioned before upgrading to VCF 9.1')}"
        if c.get("blocker"):
            workflow_blockers.append(msg)
        else:
            workflow_notes.append(msg)
        consolidation_actions.append({"component": "fleet_management_appliance", "action": c.get("action", "decommission_required")})

    idb = consolidations.get("vmware_identity_broker", {})
    consolidation_actions.append({
        "component": "vmware_identity_broker",
        "action": idb.get("action", "consolidate_into_vcf_management_services"),
        "note": idb.get("note", ""),
        "management_network_downtime": idb.get("management_network_downtime", False),
        "non_management_network_downtime": idb.get("non_management_network_downtime", True),
    })

    lic = consolidations.get("vcf_license_server", {})
    workflow_notes.append(f"[New Requirement] {lic.get('note', 'Centralized VCF License Server is required in VCF 9.1')}")
    consolidation_actions.append({"component": "vcf_license_server", "action": lic.get("action", "new_required_component")})

    return {
        "required_workflow": required_workflow,
        "workflow_blockers": workflow_blockers,
        "workflow_notes": workflow_notes,
        "consolidation_actions": consolidation_actions,
        "vcf_management_services_requirements": mgmt_svc_reqs,
        "kb_reference": paths.get("kb_reference", "https://knowledge.broadcom.com/external/article/440630"),
        "detected": {
            "has_sddc_manager": has_sddc,
            "has_nsx_4x": nsx_major == "4",
            "has_fleet_management_appliance": has_fleet_mgmt_appliance,
            "has_aria_automation": has_aria_automation,
            "has_vrslcm": has_vrslcm,
        },
    }


def _check_hardware_hcl(hosts: list, esxi_version: str, data: dict) -> list[dict]:
    esxi_major = _ver_major(esxi_version)
    hcl = data.get("hardware_hcl", {})
    certified_map = hcl.get(esxi_major, hcl.get("9.1", hcl.get("9.0", {})))
    platform_names = data.get("platform_names", {})
    hcl_url = data.get("hcl_reference_url", "https://compatibilitymatrix.broadcom.com/")

    seen: set[str] = set()
    results = []
    for host in hosts:
        cpu = host.get("cpu_model", "")
        if not cpu:
            continue
        platform = _detect_platform(cpu)
        if platform in seen:
            continue
        seen.add(platform)

        certified = certified_map.get(platform, "warning")
        friendly = platform_names.get(platform, platform)
        results.append({
            "host": host.get("name", "unknown"),
            "cpu_model": cpu,
            "platform": platform,
            "platform_name": friendly,
            "esxi_version": esxi_major,
            "certified": certified,
            "hcl_url": hcl_url,
        })

    return results


def _safe_cv(cv: dict, key: str) -> str:
    """Extract version string from component-versions dict, handling list or dict values."""
    val = cv.get(key)
    if isinstance(val, dict):
        return val.get("version", "")
    if isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                v = item.get("version", "")
                if v:
                    return v
    return ""


async def check_broadcom_interop() -> dict:
    sddc_collector_url = os.getenv("SDDC_COLLECTOR_URL", "http://collector-sddc:8011")

    async with httpx.AsyncClient(timeout=20.0) as client:
        async def _get(url: str) -> dict:
            try:
                r = await client.get(url)
                return r.json() if r.status_code == 200 else {}
            except Exception as e:
                logger.warning(f"Collector call failed {url}: {e}")
                return {}

        versions_raw, comp_versions_raw, hosts_raw, cfg_raw, sddc_system, sddc_hosts, fleet_raw = \
            await __import__("asyncio").gather(
                _get(f"{VCENTER_COLLECTOR_URL}/collect/versions"),
                _get(f"{VROPS_COLLECTOR_URL}/collect/component-versions"),
                _get(f"{VROPS_COLLECTOR_URL}/collect/host-details"),
                _get(f"{CONFIG_STORE_URL}/config/raw"),
                _get(f"{sddc_collector_url}/collect/system"),
                _get(f"{sddc_collector_url}/collect/hosts"),
                _get(f"{VROPS_COLLECTOR_URL}/collect/fleet"),
            )

    data = _load_data()
    target = cfg_raw.get("vcf_target_version", "9.1")

    # Resolve component versions — priority: vROps > vCenter collector > SDDC collector
    cv = comp_versions_raw.get("versions", {})
    vv = versions_raw.get("versions", {})
    vrops_hosts = hosts_raw.get("hosts", [])
    sddc_host_list = sddc_hosts.get("hosts", [])

    # ESXi version: vROps host-details → SDDC hosts (reliable on VCF 9.x)
    esxi_from_vrops = vrops_hosts[0].get("esxi_version", "") if vrops_hosts else ""
    esxi_from_sddc = sddc_host_list[0].get("esxi_version", "") if sddc_host_list else ""
    esxi_ver = esxi_from_vrops or vv.get("esxi", "") or esxi_from_sddc

    # SDDC Manager version directly from its system endpoint
    sddc_ver_from_collector = sddc_system.get("version", "")

    components = {
        "vcenter":      _safe_cv(cv, "vcenter") or vv.get("vcenter", ""),
        "esxi":         esxi_ver,
        "nsx":          _safe_cv(cv, "nsx_manager") or vv.get("nsx", ""),
        "sddc_manager": _safe_cv(cv, "sddc_manager") or vv.get("sddc_manager", "") or sddc_ver_from_collector,
    }

    # Normalize SDDC hosts to the shape expected by _check_hardware_hcl
    # (vrops hosts already have name/cpu_model; SDDC hosts have fqdn/hardware_model)
    normalized_sddc_hosts = [
        {
            "name": h.get("fqdn", h.get("name", "unknown")),
            "cpu_model": h.get("cpu_model", "") or h.get("hardware_model", ""),
            "esxi_version": h.get("esxi_version", ""),
        }
        for h in sddc_host_list
    ]

    # Use SDDC hosts for HCL check if vROps has no data
    hosts = vrops_hosts if vrops_hosts else normalized_sddc_hosts

    # Interop matrix check
    interop_gaps = _check_interop_matrix(components, target, data)

    # Upgrade path check (use SDDC Manager version as the VCF version indicator)
    sddc_ver = components["sddc_manager"] or components["vcenter"]
    path_gaps = _check_upgrade_path(sddc_ver, target, data)

    # Upgrade workflow determination (KB440630)
    upgrade_workflow = _determine_upgrade_workflow(components, fleet_raw, target, data)

    # Hardware HCL check
    esxi_ver = components["esxi"] or target
    hcl_results = _check_hardware_hcl(hosts, esxi_ver, data)

    hcl_gaps = [
        f"Host hardware NOT HCL-certified: {r['platform_name']} on ESXi {r['esxi_version']} — "
        f"verify at {r['hcl_url']}"
        for r in hcl_results if r["certified"] is False
    ]
    hcl_warnings = [
        f"Hardware certification unconfirmed for {r['platform_name']} on ESXi {r['esxi_version']} — "
        f"validate at {r['hcl_url']}"
        for r in hcl_results if r["certified"] == "warning"
    ]

    # Deprecation warnings for target version
    dep_data = data.get("deprecated_in_9_1", {}) if target in ("9.1",) else {}
    deprecation_warnings = []
    if dep_data:
        for category, items in dep_data.items():
            for item in items:
                deprecation_warnings.append(f"[Deprecation/{category}] {item}")

    workflow_blockers = upgrade_workflow.get("workflow_blockers", [])
    all_gaps = interop_gaps + path_gaps + hcl_gaps + workflow_blockers
    evidence = [
        {"source": "BROADCOM_INTEROP", "metric": f"{k}_version", "value": v or "unknown",
         "threshold": f"VCF {target} interop matrix"}
        for k, v in components.items()
    ] + [
        {"source": "BROADCOM_HCL",
         "metric": f"hw_hcl_{r['platform']}",
         "value": r["platform_name"],
         "threshold": f"ESXi {r['esxi_version']} certified"}
        for r in hcl_results
    ]

    workflow_notes = upgrade_workflow.get("workflow_notes", [])
    return {
        "interop_gaps": all_gaps,
        "interop_warnings": hcl_warnings + deprecation_warnings + workflow_notes,
        "deprecation_warnings": deprecation_warnings,
        "components": components,
        "hcl_results": hcl_results,
        "target_version": target,
        "upgrade_workflow": upgrade_workflow,
        "evidence": evidence,
        "normalized": {
            "entities": [{
                "entity_type": "broadcom_interop",
                "name": f"VCF {target} Interoperability & HCL",
                "interop_gaps": all_gaps,
                "interop_warnings": hcl_warnings,
                "deprecation_warnings": deprecation_warnings,
                "hcl_results": hcl_results,
                "upgrade_workflow": upgrade_workflow,
            }]
        },
    }
