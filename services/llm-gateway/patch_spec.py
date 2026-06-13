#!/usr/bin/env python3
"""
Patches vcenter_api_spec.json:
  1. Fills empty use_when on all GET endpoints using path-shape inference.
  2. Fixes known description bugs (e.g. GET /vcenter/namespaces/instances).
  3. Adds domain-specific retrieval terms for vSphere Namespaces / Supervisor endpoints.

Run once, then commit the updated spec. Safe to re-run (idempotent on already-tagged entries).
"""
import json, re
from pathlib import Path

SPEC = Path(__file__).parent / "vcenter_api_spec.json"

# ---------------------------------------------------------------------------
# Extra domain-specific tags for endpoints the generic logic won't name well
# ---------------------------------------------------------------------------
EXTRA_TAGS: dict[tuple[str, str], list[str]] = {
    ("GET", "/vcenter/namespaces/instances"): [
        "list supervisor namespaces", "list namespaces", "all namespaces",
        "show namespaces", "supervisor namespaces", "vSphere namespaces",
        "list all namespaces",
    ],
    ("GET", "/vcenter/namespaces/instances/{instances}"): [
        "namespace details", "get namespace", "namespace by id",
        "supervisor namespace info",
    ],
    ("GET", "/vcenter/namespace_management/namespaces"): [
        "list namespaces", "namespace management", "all namespaces",
    ],
    ("GET", "/vcenter/namespace_management/supervisors"): [
        "list supervisors", "all supervisors", "vSphere supervisors",
        "supervisor clusters",
    ],
    ("GET", "/vcenter/namespace_management/supervisors/{supervisors}"): [
        "supervisor details", "get supervisor", "supervisor info",
    ],
    ("GET", "/vcenter/namespace_management/cluster_compatibility"): [
        "namespace cluster compatibility", "supervisor compatibility",
        "cluster ready for namespaces",
    ],
    ("GET", "/vcenter/namespace_management/supervisor_services"): [
        "list supervisor services", "all supervisor services",
        "installed supervisor services",
    ],
    ("GET", "/vcenter/namespaces/user/instances"): [
        "list user namespaces", "user namespace instances",
        "dev namespace list",
    ],
    ("GET", "/vcenter/vcha/cluster"): [
        "vcha cluster status", "vCenter HA status", "high availability status",
    ],
    ("GET", "/vcenter/certificate_management/vcenter/tls"): [
        "vCenter TLS certificate", "vcenter cert", "tls certificate details",
    ],
    ("GET", "/esx/hcl/compatibility_data"): [
        "HCL compatibility data", "hardware compatibility list",
        "ESXi HCL status",
    ],
    ("GET", "/vcenter/trusted_infrastructure/trusted_clusters/attestation/services"): [
        "attestation services", "trusted cluster attestation",
    ],
    ("GET", "/vcenter/deployment/upgrade"): [
        "vCenter upgrade status", "deployment upgrade",
        "vCenter upgrade readiness",
    ],
}

# Known description corrections — key = (method, path)
DESCRIPTION_FIX: dict[tuple[str, str], str] = {
    ("GET", "/vcenter/namespaces/instances"):
        "List all supervisor namespaces (vSphere Namespaces instances) across all Supervisors.",
}


def infer_use_when(path: str) -> list[str]:
    """Generate use_when tags from path shape for a GET endpoint with no tags."""
    segs = [s for s in path.strip("/").split("/") if s]
    # Drop path params — {foo}
    clean = [s for s in segs if not s.startswith("{")]
    if not clean:
        return []

    noun = clean[-1].replace("_", " ").replace("-", " ")
    parent = clean[-2].replace("_", " ").replace("-", " ") if len(clean) > 1 else ""

    # Ends with a path param → single-item "get" operation
    if segs and segs[-1].startswith("{"):
        tags = [f"{noun} details", f"get specific {noun}", f"{noun} by id"]
        if parent:
            tags.append(f"{noun} in {parent}")
        return tags

    # Otherwise → "list" operation
    tags = [f"list {noun}", f"all {noun}", f"show {noun}", f"get {noun}"]
    if parent:
        tags.append(f"{noun} in {parent}")
    return tags


def main():
    spec = json.loads(SPEC.read_text())
    endpoints = spec["endpoints"]

    patched = fixed_desc = added_extra = 0

    for ep in endpoints:
        key = (ep["method"], ep["path"])

        # Fix wrong descriptions
        if key in DESCRIPTION_FIX:
            ep["description"] = DESCRIPTION_FIX[key]
            fixed_desc += 1

        # Fill empty use_when on GET endpoints
        if ep["method"] == "GET" and not ep.get("use_when"):
            ep["use_when"] = infer_use_when(ep["path"])
            patched += 1

        # Merge extra domain-specific tags (deduplicated)
        if key in EXTRA_TAGS:
            existing = set(ep.get("use_when") or [])
            new_tags = [t for t in EXTRA_TAGS[key] if t not in existing]
            ep["use_when"] = list(existing) + new_tags
            if new_tags:
                added_extra += 1

    SPEC.write_text(json.dumps(spec, indent=2))
    print(f"Patched {patched} empty GET use_when entries")
    print(f"Fixed {fixed_desc} wrong descriptions")
    print(f"Added extra domain tags to {added_extra} endpoints")
    print(f"Spec written to {SPEC}")


if __name__ == "__main__":
    main()
