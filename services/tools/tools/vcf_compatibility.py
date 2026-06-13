import json
import os
import logging
import httpx
from pathlib import Path

logger = logging.getLogger("tool.vcf_compatibility")

VCENTER_COLLECTOR_URL = os.getenv("VCENTER_COLLECTOR_URL", "http://collector-vcenter:8003")
CONFIG_STORE_URL = os.getenv("CONFIG_STORE_URL", "http://config-store:8009")

_DATA_FILE = Path(__file__).parent.parent / "data" / "vcf_interop.json"


def _load_interop() -> dict:
    try:
        return json.loads(_DATA_FILE.read_text())
    except Exception as e:
        logger.warning(f"Cannot load vcf_interop.json: {e}")
        return {}


def _min_source_versions(target: str, data: dict) -> dict:
    """
    Return the minimum component versions the environment must be on
    to qualify for a direct upgrade to `target`.

    For 9.1: supported sources are 9.0 and 5.2.
      - If the environment is already on 9.0.x → min is 9.0.0
      - If on 5.2.x → min is 5.2.0 / 8.0.2 for vCenter+ESXi
    We return the least restrictive set so any supported source passes.
    For the 9.x family the practical minimum is 9.0.0.
    """
    paths = data.get("upgrade_paths", {}).get(target, {})
    sources = paths.get("supported_direct_sources", [])

    # VCF 9.x targets: lowest supported direct source is 9.0
    if any(s.startswith("9.") for s in sources):
        return {"vcenter": "9.0.0", "esxi": "9.0.0", "nsx": "9.0.0", "sddc_manager": "9.0.0"}
    # VCF 5.x targets
    if any(s.startswith("5.") for s in sources):
        return {"vcenter": "8.0.2", "esxi": "8.0.2", "nsx": "4.1.0", "sddc_manager": "5.2.0"}
    return {}


async def check_vcf_compatibility() -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            cfg = (await client.get(f"{CONFIG_STORE_URL}/config/raw")).json()
        except Exception:
            cfg = {}
        resp = await client.get(f"{VCENTER_COLLECTOR_URL}/collect/versions")
        resp.raise_for_status()
        raw = resp.json()

    target = cfg.get("vcf_target_version", "9.1")
    data = _load_interop()
    min_versions = _min_source_versions(target, data)

    compatibility_gaps = []
    for component, min_ver in min_versions.items():
        actual = raw.get("versions", {}).get(component, "")
        if not actual:
            # Version not available from vCenter collector — skip; broadcom_interop covers this
            continue
        # Strip build number (e.g. "9.0.2-25148076" → "9.0.2") before comparing
        actual_clean = actual.split("-")[0]
        if _version_lt(actual_clean, min_ver):
            compatibility_gaps.append(
                f"{component} version {actual} is below required {min_ver} for VCF {target} upgrade"
            )

    evidence = [
        {
            "source": "VCENTER",
            "metric": f"{comp}_version",
            "value": ver,
            "threshold": min_versions.get(comp, "N/A"),
        }
        for comp, ver in raw.get("versions", {}).items() if ver
    ]

    normalized = {
        "entities": [{
            "entity_type": "compatibility_check",
            "name": f"VCF {target} Compatibility",
            "compatibility_gaps": compatibility_gaps,
            "versions": raw.get("versions", {}),
            "timestamp": raw.get("timestamp"),
        }]
    }

    return {"normalized": normalized, "evidence": evidence, "raw": raw, "compatibility_gaps": compatibility_gaps}


def _version_lt(v1: str, v2: str) -> bool:
    try:
        parts1 = [int(x) for x in v1.split(".")]
        parts2 = [int(x) for x in v2.split(".")]
        max_len = max(len(parts1), len(parts2))
        parts1 += [0] * (max_len - len(parts1))
        parts2 += [0] * (max_len - len(parts2))
        return parts1 < parts2
    except (ValueError, AttributeError):
        return False
