"""
Workspace API executor — proxies safe (non-DELETE) API calls to vCenter, vROps, or PowerCLI.
Credentials are fetched live from config-store on every call.
"""
import os
import time
import logging
import httpx

logger = logging.getLogger("api-gateway.workspace")

_FORBIDDEN = {"DELETE", "PATCH"}
POWERCLI_URL = os.getenv("POWERCLI_URL", "http://powercli:8010")


async def _get_cfg(config_store_url: str) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{config_store_url}/config/raw")
        resp.raise_for_status()
        return resp.json()


async def _vcenter_session(cfg: dict) -> tuple[str, str, bool]:
    host = cfg.get("vcenter_host", "")
    verify = cfg.get("vcenter_verify_ssl", False)
    async with httpx.AsyncClient(verify=verify, timeout=15.0) as client:
        resp = await client.post(
            f"https://{host}/api/session",
            auth=(cfg.get("vcenter_user", ""), cfg.get("vcenter_password", "")),
        )
        resp.raise_for_status()
        return host, resp.json(), verify


async def _vrops_token(cfg: dict) -> tuple[str, str, bool]:
    host = cfg.get("vrops_host", "")
    verify = cfg.get("vrops_verify_ssl", False)
    async with httpx.AsyncClient(verify=verify, timeout=15.0) as client:
        resp = await client.post(
            f"https://{host}/suite-api/api/auth/token/acquire",
            json={"username": cfg.get("vrops_user", ""), "password": cfg.get("vrops_password", ""), "authSource": "LOCAL"},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        resp.raise_for_status()
        return host, resp.json().get("token", ""), verify


# Paths permitted in the vCenter REST API (after normalization strips /api prefix).
# Derived from vcenter_api_spec.json — update when new API categories are added.
_VCENTER_ALLOWED_PREFIXES = (
    "/vcenter/", "/appliance/", "/cis/", "/content/", "/esx/", "/rest/",
)


def _normalize_vcenter_path(path: str) -> str:
    # Strip escaped forward slashes from LLM output (e.g. \/vcenter\/vm → /vcenter/vm)
    path = path.replace("\\/", "/")
    # Return /rest/ paths as-is (they get prepended to host directly)
    if path.startswith("/rest/"):
        return path
    # Strip /api/ prefix so we can re-add it consistently
    if path.startswith("/api/"):
        path = path[4:]
    if not path.startswith("/"):
        path = "/" + path
    return path


def _rewrite_vcenter_path(method: str, path: str) -> str:
    """Redirect known-broken /api/ endpoints to their working equivalents."""
    # POST /vcenter/vm must use /rest/ — /api/vcenter/vm has vAPI type resolution issues
    if method == "POST" and path.rstrip("/") in ("/vcenter/vm", "/api/vcenter/vm", "/rest/vcenter/vm"):
        return "/rest/vcenter/vm"
    # /vcenter/system/version doesn't exist on VCF 9.x; appliance version endpoint works
    stripped = path.lstrip("/api").rstrip("/") if path.startswith("/api/") else path.rstrip("/")
    if stripped in ("/vcenter/system/version", "/appliance/system/version"):
        return "/appliance/system/version"
    return path


def _strip_filter_prefix(query_params: dict) -> dict:
    """VCF 9.x vCenter does not support filter.* query params; strip the prefix."""
    if not query_params:
        return query_params
    fixed = {}
    for k, v in query_params.items():
        bare = k[len("filter."):] if k.startswith("filter.") else k
        fixed[bare] = v
    return fixed


_VM_SPEC_KEYS = {"name", "guest_OS", "placement", "cpu", "memory", "disks", "nics",
                 "hardware_version", "boot", "storage_policy"}
_PLACEMENT_KEYS = {"cluster", "datastore", "folder", "host", "resource_pool"}
_DISK_VMDK_KEYS = {"capacity", "name", "storage_policy"}


def _sanitize_vm_body(body: dict) -> dict:
    """Strip LLM hallucinated fields from a VM create body before sending to vCenter."""
    if not isinstance(body, dict) or "spec" not in body:
        return body
    raw = body["spec"]
    if not isinstance(raw, dict):
        return {"spec": {}}  # malformed spec — send nothing rather than garbage
    clean = {k: v for k, v in raw.items() if k in _VM_SPEC_KEYS}

    # Fix guest_OS: must be a string, not list/dict
    if isinstance(clean.get("guest_OS"), list):
        clean["guest_OS"] = clean["guest_OS"][0]
    if isinstance(clean.get("guest_OS"), dict):
        clean["guest_OS"] = "OTHER_LINUX_64"

    # Fix placement: only allow known keys
    if isinstance(clean.get("placement"), dict):
        clean["placement"] = {k: v for k, v in clean["placement"].items() if k in _PLACEMENT_KEYS}

    # Fix disks: strip unknown fields, ensure capacity is int, keep only new_vmdk.capacity/name
    if isinstance(clean.get("disks"), list):
        fixed_disks = []
        for d in clean["disks"]:
            if not isinstance(d, dict):
                continue
            disk = {}
            if "type" in d and isinstance(d["type"], str):
                disk["type"] = d["type"]
            if "new_vmdk" in d and isinstance(d["new_vmdk"], dict):
                vmdk = {k: v for k, v in d["new_vmdk"].items() if k in _DISK_VMDK_KEYS}
                # capacity must be an integer (LLM sometimes produces strings)
                if "capacity" in vmdk:
                    try:
                        vmdk["capacity"] = int(vmdk["capacity"])
                    except (TypeError, ValueError):
                        vmdk.pop("capacity", None)
                disk["new_vmdk"] = vmdk
            if disk.get("new_vmdk"):
                fixed_disks.append(disk)
        clean["disks"] = fixed_disks

    # Fix nics: LLM often adds garbage NIC objects — only allow empty array or valid backing
    if isinstance(clean.get("nics"), list):
        valid_nics = [n for n in clean["nics"] if isinstance(n, dict) and "backing" in n]
        clean["nics"] = valid_nics  # empty if no valid backing

    # Fix memory: ensure size_MiB is int, strip unknown fields
    if isinstance(clean.get("memory"), dict):
        mem = {k: v for k, v in clean["memory"].items() if k in ("size_MiB", "hot_add_enabled")}
        if "size_MiB" in mem:
            try:
                mem["size_MiB"] = int(mem["size_MiB"])
            except (TypeError, ValueError):
                pass
        clean["memory"] = mem

    # Fix cpu: ensure count/cores_per_socket are ints
    if isinstance(clean.get("cpu"), dict):
        for k in ("count", "cores_per_socket"):
            if k in clean["cpu"]:
                try:
                    clean["cpu"][k] = int(clean["cpu"][k])
                except (TypeError, ValueError):
                    pass

    return {"spec": clean}


def _normalize_vrops_path(path: str) -> str:
    for prefix in ("/suite-api/api", "/suite-api"):
        if path.startswith(prefix):
            path = path[len(prefix):]
    if not path.startswith("/"):
        path = "/" + path
    return path


def _normalize_sddc_path(path: str) -> str:
    # Strip leading /sddc-manager prefix LLM sometimes adds
    for prefix in ("/sddc-manager/api", "/sddc-manager"):
        if path.startswith(prefix):
            path = path[len(prefix):]
    if not path.startswith("/"):
        path = "/" + path
    return path


async def _call_sddc_manager(cfg: dict, method: str, path: str, body, query_params: dict) -> dict:
    host = cfg.get("sddc_host", "")
    if not host:
        return {"status_code": 503, "response": {"error": "SDDC Manager not configured"}}
    user = cfg.get("sddc_user", "administrator@vsphere.local")
    password = cfg.get("sddc_password", "")
    verify = cfg.get("sddc_verify_ssl", False)

    path = _normalize_sddc_path(path)

    async with httpx.AsyncClient(verify=verify, timeout=30.0) as client:
        token_resp = await client.post(
            f"https://{host}/v1/tokens",
            json={"username": user, "password": password},
            headers={"Content-Type": "application/json"},
        )
        token_resp.raise_for_status()
        token = token_resp.json()["accessToken"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}

        url = f"https://{host}{path}"
        resp = await client.request(
            method, url, headers=headers,
            params=query_params or None,
            json=body if body and method in ("POST", "PUT", "PATCH") else None,
            timeout=30.0,
        )
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
        return {"status_code": resp.status_code, "response": data}


async def execute(spec: dict, config_store_url: str) -> dict:
    method = spec.get("method", "GET").upper()
    if method in _FORBIDDEN:
        return {"status_code": 403, "elapsed_ms": 0, "response": {"error": f"{method} method is not permitted in Workspace"}}

    target = spec.get("target", "vcenter").lower()
    path = spec.get("path", "/")
    body = spec.get("body")
    query_params = spec.get("query_params") or {}

    try:
        cfg = await _get_cfg(config_store_url)
    except Exception as e:
        return {"status_code": 503, "elapsed_ms": 0, "response": {"error": f"Cannot reach config-store: {e}"}}

    start = time.monotonic()
    try:
        if target == "vcenter":
            raw = await _call_vcenter(cfg, method, path, body, query_params)
        elif target == "vrops":
            raw = await _call_vrops(cfg, method, path, body, query_params)
        elif target == "sddc_manager":
            raw = await _call_sddc_manager(cfg, method, path, body, query_params)
        elif target == "powercli":
            raw = await _call_powercli(cfg, body)
        elif target == "ad":
            raw = await _call_ad(cfg, body)
        else:
            return {"status_code": 400, "elapsed_ms": 0, "response": {"error": f"Unknown target: {target}"}}
        elapsed_ms = round((time.monotonic() - start) * 1000)
        return {"status_code": raw["status_code"], "elapsed_ms": elapsed_ms, "response": raw["response"]}
    except httpx.HTTPStatusError as e:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        try:
            body_out = e.response.json()
        except Exception:
            body_out = {"raw": e.response.text}
        return {"status_code": e.response.status_code, "elapsed_ms": elapsed_ms, "response": body_out}
    except Exception as e:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        logger.error(f"Workspace execute error: {e}")
        return {"status_code": 500, "elapsed_ms": elapsed_ms, "response": {"error": str(e)}}


async def _call_vcenter(cfg: dict, method: str, path: str, body, query_params: dict) -> dict:
    host = cfg.get("vcenter_host", "")
    if not host:
        return {"status_code": 503, "response": {"error": "vCenter not configured in Settings"}}
    if ".." in path:
        return {"status_code": 400, "response": {"error": "Invalid path"}}
    path = _rewrite_vcenter_path(method, path)
    path = _normalize_vcenter_path(path)
    if not any(path.startswith(p) for p in _VCENTER_ALLOWED_PREFIXES):
        return {"status_code": 403, "response": {"error": f"Path not in allowed vCenter API prefixes: {path}"}}
    query_params = _strip_filter_prefix(query_params)
    # Sanitize VM create body to remove LLM hallucinated fields
    if method == "POST" and path == "/rest/vcenter/vm" and isinstance(body, dict):
        body = _sanitize_vm_body(body)
    host, token, verify = await _vcenter_session(cfg)
    # /rest/ paths are fully-qualified; all others get /api prefix
    if path.startswith("/rest/"):
        url = f"https://{host}{path}"
    else:
        url = f"https://{host}/api{path}"
    headers = {"vmware-api-session-id": token, "Content-Type": "application/json", "Accept": "application/json"}
    async with httpx.AsyncClient(verify=verify, timeout=30.0) as client:
        resp = await client.request(method=method, url=url, headers=headers,
                                    params=query_params or None, json=body if body else None)
    try:
        return {"status_code": resp.status_code, "response": resp.json()}
    except Exception:
        return {"status_code": resp.status_code, "response": {"raw": resp.text}}


async def _call_powercli(cfg: dict, body) -> dict:
    """Execute a PowerCLI script via the powercli runner service."""
    if not isinstance(body, dict) or not body.get("script"):
        return {"status_code": 400, "response": {"error": "PowerCLI body must contain 'script'"}}
    host = cfg.get("vcenter_host", "")
    if not host:
        return {"status_code": 503, "response": {"error": "vCenter not configured in Settings"}}
    payload = {
        "script": body["script"],
        "vcenter_host": host,
        "vcenter_user": cfg.get("vcenter_user", ""),
        "vcenter_password": cfg.get("vcenter_password", ""),
        "verify_ssl": cfg.get("vcenter_verify_ssl", False),
        # Forward allow_writes flag from the workspace request body
        "allow_writes": bool(body.get("allow_writes", False)),
    }
    async with httpx.AsyncClient(timeout=150.0) as client:
        resp = await client.post(f"{POWERCLI_URL}/run", json=payload)
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    # Normalize: powercli service returns its own status_code
    status = data.pop("status_code", resp.status_code)
    return {"status_code": status, "response": data}


async def _call_ad(cfg: dict, body) -> dict:
    """Execute a script against Active Directory via ADSI (no vCenter connection)."""
    if not cfg.get("ad_host"):
        return {"status_code": 503, "response": {"error": "Active Directory not configured in Settings"}}
    if not isinstance(body, dict) or not body.get("script"):
        return {"status_code": 400, "response": {"error": "AD body must contain 'script'"}}
    payload = {
        "script": body["script"],
        "skip_vcenter_connect": True,
        "ad_host": cfg.get("ad_host", ""),
        "ad_user": cfg.get("ad_user", ""),
        "ad_password": cfg.get("ad_password", ""),
    }
    async with httpx.AsyncClient(timeout=150.0) as client:
        resp = await client.post(f"{POWERCLI_URL}/run", json=payload)
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    status = data.pop("status_code", resp.status_code)
    return {"status_code": status, "response": data}


async def _call_vrops(cfg: dict, method: str, path: str, body, query_params: dict) -> dict:
    host = cfg.get("vrops_host", "")
    if not host:
        return {"status_code": 503, "response": {"error": "vROps not configured in Settings"}}
    path = _normalize_vrops_path(path)
    host, token, verify = await _vrops_token(cfg)
    url = f"https://{host}/suite-api/api{path}"
    headers = {"Authorization": f"vRealizeOpsToken {token}", "Accept": "application/json", "Content-Type": "application/json"}
    async with httpx.AsyncClient(verify=verify, timeout=30.0) as client:
        resp = await client.request(method=method, url=url, headers=headers,
                                    params=query_params or None, json=body if body else None)
    try:
        return {"status_code": resp.status_code, "response": resp.json()}
    except Exception:
        return {"status_code": resp.status_code, "response": {"raw": resp.text}}
