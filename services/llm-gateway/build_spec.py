#!/usr/bin/env python3
"""
One-shot spec builder: crawls the live vCenter vAPI metamodel and generates
vcenter_api_spec.json for the BM25 RAG index.

Run: python3 build_spec.py  (inside the container or locally with vCenter reachable)
"""

import httpx
import json
import sys
import re
from pathlib import Path

CONFIG_STORE_URL = "http://config-store:8009"
OUT_PATH = Path(__file__).parent / "vcenter_api_spec.json"

# Which service prefixes to index
INCLUDE_PREFIXES = (
    "com.vmware.vcenter",
    "com.vmware.appliance",
    "com.vmware.content",
    "com.vmware.cis.tagging",
    "com.vmware.esx.settings",
    "com.vmware.esx.hcl",
)

# vAPI operation name → HTTP method
OP_METHOD = {
    "list":   "GET",
    "get":    "GET",
    "create": "POST",
    "add":    "POST",
    "set":    "PUT",
    "update": "PUT",
    "delete": "DELETE",
}

# Short descriptions for well-known service segments
SEGMENT_DESC = {
    "vm": "virtual machines",
    "cluster": "clusters",
    "host": "ESXi hosts",
    "datastore": "datastores",
    "network": "networks and port groups",
    "datacenter": "datacenters",
    "folder": "folders",
    "resource-pool": "resource pools",
    "storage/policies": "storage policies",
    "system/version": "vCenter system version and build",
    "content/library": "content library items",
    "tagging/category": "tag categories",
    "tagging/tag": "tags",
    "appliance/health": "vCenter appliance health",
    "appliance/networking": "vCenter appliance network configuration",
    "appliance/services": "vCenter appliance services (start/stop)",
    "appliance/ntp": "NTP server configuration",
}


def svc_to_path(svc_id: str) -> str:
    """
    com.vmware.vcenter.vm          → /vcenter/vm
    com.vmware.vcenter.vm.hardware → /vcenter/vm/{vm}/hardware
    com.vmware.appliance.health    → /appliance/health
    com.vmware.cis.tagging.tag     → /cis/tagging/tag
    com.vmware.content.library     → /content/library
    """
    s = svc_id.replace("com.vmware.", "")
    # Replace dots with slashes
    return "/" + s.replace(".", "/")


def build_use_when(path: str, method: str, op_name: str) -> list[str]:
    all_segs = [p for p in path.strip("/").split("/")]
    segs = [p for p in all_segs if not p.startswith("{")]
    noun = segs[-1] if segs else ""
    parent = segs[-2] if len(segs) > 1 else ""
    terms = []
    if method == "GET":
        # Infer list vs get from op_name OR path shape (ends with {param} → get, else → list)
        is_list = op_name == "list" or (op_name not in ("get",) and not all_segs[-1].startswith("{"))
        if is_list:
            terms += [f"list {noun}", f"all {noun}", f"show {noun}", f"get {noun}"]
            if parent:
                terms += [f"{noun} in {parent}"]
        else:
            terms += [f"{noun} details", f"get specific {noun}", f"{noun} by id"]
            if parent:
                terms += [f"{noun} in {parent}"]
    elif method == "POST":
        terms += [f"create {noun}", f"new {noun}", f"add {noun}", f"deploy {noun}"]
    elif method in ("PUT", "PATCH"):
        terms += [f"update {noun}", f"modify {noun}", f"change {noun}"]
    elif method == "DELETE":
        terms += [f"delete {noun}", f"remove {noun}"]
    return terms


def extract_params(op: dict) -> list[str]:
    """Extract input parameter names from a vAPI operation definition."""
    params = []
    for p in op.get("params", []):
        name = p.get("name", "")
        if name and not name.startswith("~"):
            params.append(name)
    return params


def main():
    print("Fetching vCenter credentials from config-store...")
    cfg = httpx.get(f"{CONFIG_STORE_URL}/config/raw", timeout=5).json()
    host = cfg.get("vcenter_host", "")
    user = cfg.get("vcenter_user", "administrator@vsphere.local")
    password = cfg.get("vcenter_password", "")
    verify = cfg.get("vcenter_verify_ssl", False)

    if not host or not password:
        print("ERROR: vCenter not configured in config-store", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to vCenter {host}...")
    with httpx.Client(verify=verify, timeout=15) as client:
        token_resp = client.post(f"https://{host}/api/session", auth=(user, password))
        if token_resp.status_code != 201:
            print(f"ERROR: Could not get vCenter session: {token_resp.status_code}", file=sys.stderr)
            sys.exit(1)
        token = token_resp.json()
        headers = {"vmware-api-session-id": token}

        # Get all service IDs
        svc_list_resp = client.get(
            f"https://{host}/rest/com/vmware/vapi/metadata/metamodel/service",
            headers=headers,
        )
        all_services = svc_list_resp.json().get("value", [])
        print(f"Total services in metamodel: {len(all_services)}")

        relevant = [s for s in all_services if s.startswith(INCLUDE_PREFIXES)]
        print(f"Relevant services to index: {len(relevant)}")

        endpoints = []
        errors = 0

        for i, svc_id in enumerate(relevant):
            try:
                resp = client.post(
                    f"https://{host}/rest/com/vmware/vapi/metadata/metamodel/service?~action=get",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"service_id": svc_id},
                    timeout=10,
                )
                if resp.status_code != 200:
                    errors += 1
                    continue

                svc = resp.json().get("value", {})
                ops = svc.get("operations", [])

                for op_entry in ops:
                    op_name = op_entry.get("key", "")
                    op = op_entry.get("value", {})

                    method = OP_METHOD.get(op_name, "GET")
                    base_path = svc_to_path(svc_id)

                    # For "get"/"update"/"delete" operations, append /{id}
                    if op_name in ("get", "update", "delete", "set"):
                        # derive the ID param name from the last path segment
                        seg = base_path.strip("/").split("/")[-1]
                        id_param = seg.replace("-", "_")
                        path = f"{base_path}/{{{id_param}}}"
                    else:
                        path = base_path

                    params = extract_params(op)
                    doc = op.get("documentation", "")

                    # Build description
                    noun = base_path.strip("/").split("/")[-1].replace("-", " ").replace("_", " ")
                    if not doc:
                        verb = {"GET": "List" if op_name == "list" else "Get",
                                "POST": "Create", "PUT": "Update", "DELETE": "Delete"}.get(method, "Access")
                        doc = f"{verb} {noun}"

                    endpoint = {
                        "method": method,
                        "path": path,
                        "description": doc,
                        "query_params": {p: "" for p in params if method == "GET"},
                        "returns": f"{noun} data",
                        "use_when": build_use_when(path, method, op_name),
                    }
                    endpoints.append(endpoint)

                if (i + 1) % 20 == 0:
                    print(f"  Processed {i+1}/{len(relevant)} services, {len(endpoints)} endpoints so far...")

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  WARN: {svc_id}: {e}")

    # Deduplicate by (method, path)
    seen = set()
    unique = []
    for ep in endpoints:
        key = (ep["method"], ep["path"])
        if key not in seen:
            seen.add(key)
            unique.append(ep)

    # Filter out DELETE endpoints (we don't expose those)
    unique = [ep for ep in unique if ep["method"] != "DELETE"]

    print(f"\nTotal endpoints extracted: {len(endpoints)}")
    print(f"After dedup + filter: {len(unique)}")
    print(f"Errors: {errors}")

    spec = {"endpoints": unique}
    OUT_PATH.write_text(json.dumps(spec, indent=2))
    print(f"\nSpec written to {OUT_PATH} ({OUT_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
