"""
Cluster discovery, per-cluster client cache, and kubernetes API helpers.
Reads supervisor kubeconfig from /etc/supervisor/config (mounted as a secret).
"""
import asyncio
import base64
import logging
import os
import ssl
import tempfile
from datetime import datetime, timedelta, timezone

import httpx
import yaml

logger = logging.getLogger("vks-broker")

SUPERVISOR_KUBECONFIG = os.getenv("SUPERVISOR_KUBECONFIG", "/etc/supervisor/config")


class KubeForbiddenError(Exception):
    """Raised when the k8s API returns 401 or 403."""
    def __init__(self, status_code: int, verb: str, resource: str, namespace: str = ""):
        self.status_code = status_code
        self.verb = verb
        self.resource = resource
        self.namespace = namespace
        super().__init__(f"Forbidden: {verb} {resource}" + (f" in {namespace}" if namespace else ""))


def _check_k8s_status(resp: "httpx.Response", verb: str, resource: str, namespace: str = "") -> None:
    """Raise KubeForbiddenError on 401/403, else delegate to raise_for_status."""
    if resp.status_code in (401, 403):
        raise KubeForbiddenError(resp.status_code, verb, resource, namespace)
    resp.raise_for_status()
_CLIENT_TTL = timedelta(minutes=10)

# Namespace where vks-broker runs (for storing imported kubeconfigs)
_K8S_NAMESPACE = os.getenv("POD_NAMESPACE", "vcf-ai-ops")
_IMPORTED_SECRET_NAME = "vks-imported-configs"

# In-memory store of user-imported clusters: {cluster_id: {name, kubeconfig_yaml, server}}
_imported_clusters: dict[str, dict] = {}
_imported_lock = asyncio.Lock()

# ── kubeconfig parsing ───────────────────────────────────────────────────────

def _parse_kubeconfig_dict(kc: dict, context_name: str | None = None) -> dict:
    """Extract credentials from a parsed kubeconfig dict."""
    current = context_name or kc.get("current-context", "")
    ctx = next((c["context"] for c in kc.get("contexts", []) if c["name"] == current), None)
    if ctx is None and kc.get("contexts"):
        ctx = kc["contexts"][0]["context"]
        logger.warning("Context %r not found, using first context", current)

    cluster_name = ctx["cluster"] if ctx else ""
    user_name = ctx.get("user", "") if ctx else ""

    cluster = next((c["cluster"] for c in kc.get("clusters", []) if c["name"] == cluster_name), {})
    user = next((u["user"] for u in kc.get("users", []) if u["name"] == user_name), {})

    server = cluster.get("server", "")

    ca_data: bytes | None = None
    if cluster.get("certificate-authority-data"):
        ca_data = base64.b64decode(cluster["certificate-authority-data"])
    elif cluster.get("certificate-authority"):
        with open(cluster["certificate-authority"], "rb") as f:
            ca_data = f.read()
    insecure = cluster.get("insecure-skip-tls-verify", False)

    # Fix: read tokenFile content; don't assign the path string to token
    token = user.get("token") or ""
    if not token and user.get("tokenFile"):
        try:
            with open(user["tokenFile"]) as f:
                token = f.read().strip()
        except Exception:
            pass

    # Client cert auth
    cert_data: bytes | None = None
    key_data: bytes | None = None
    if user.get("client-certificate-data"):
        cert_data = base64.b64decode(user["client-certificate-data"])
    if user.get("client-key-data"):
        key_data = base64.b64decode(user["client-key-data"])

    return {
        "server": server,
        "token": token,
        "ca_data": ca_data,
        "cert_data": cert_data,
        "key_data": key_data,
        "insecure": insecure,
    }


def _parse_kubeconfig(path: str, context_name: str | None = None) -> dict:
    with open(path) as f:
        kc = yaml.safe_load(f)
    return _parse_kubeconfig_dict(kc, context_name)


def _parse_kubeconfig_yaml(kc_yaml: str) -> dict:
    """Parse kubeconfig from a YAML string — no tempfile."""
    return _parse_kubeconfig_dict(yaml.safe_load(kc_yaml))


def _build_client(parsed: dict) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient from parsed kubeconfig data."""
    headers = {}
    if parsed["token"]:
        headers["Authorization"] = f"Bearer {parsed['token']}"

    # TLS setup
    if parsed["insecure"]:
        verify: bool | ssl.SSLContext = False
    elif parsed["ca_data"]:
        ctx = ssl.create_default_context(cadata=parsed["ca_data"].decode("utf-8", errors="replace"))
        verify = ctx
    else:
        verify = True

    # Client cert (mutual TLS)
    cert = None
    if parsed["cert_data"] and parsed["key_data"]:
        # Write to temp files because httpx wants file paths for cert
        import atexit
        cert_f = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
        key_f = tempfile.NamedTemporaryFile(delete=False, suffix=".key")
        cert_f.write(parsed["cert_data"])
        key_f.write(parsed["key_data"])
        cert_f.close()
        key_f.close()
        cert = (cert_f.name, key_f.name)
        atexit.register(lambda: [os.unlink(cert_f.name), os.unlink(key_f.name)])

    return httpx.AsyncClient(
        base_url=parsed["server"],
        headers=headers,
        verify=verify,
        cert=cert,
        timeout=30.0,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
    )


# ── Supervisor client (singleton, re-created on token expiry) ────────────────

_supervisor_client: httpx.AsyncClient | None = None
_supervisor_lock = asyncio.Lock()


async def get_supervisor_client() -> httpx.AsyncClient:
    global _supervisor_client
    async with _supervisor_lock:
        if _supervisor_client is None:
            try:
                parsed = _parse_kubeconfig(SUPERVISOR_KUBECONFIG)
                _supervisor_client = _build_client(parsed)
                logger.info("Supervisor client created: %s", parsed["server"])
            except Exception as e:
                logger.error("Failed to build supervisor client: %s", e)
                raise RuntimeError(f"Supervisor kubeconfig unavailable: {e}")
    return _supervisor_client


async def reset_supervisor_client():
    global _supervisor_client
    async with _supervisor_lock:
        if _supervisor_client:
            await _supervisor_client.aclose()
        _supervisor_client = None


# ── Tenant cluster client cache ──────────────────────────────────────────────

# {cluster_id: (client, expires_at)}
_cluster_cache: dict[str, tuple[httpx.AsyncClient, datetime]] = {}
# {cluster_id: parsed_kubeconfig_dict}
_cluster_info: dict[str, dict] = {}
_cache_lock = asyncio.Lock()


async def get_cluster_client(cluster_id: str, namespace: str, name: str) -> httpx.AsyncClient:
    """Get a cached httpx client for a tenant cluster. Refreshes on TTL expiry.

    For imported clusters (namespace == 'imported'), uses the stored kubeconfig directly
    without going to the supervisor — works even when supervisor token is expired.
    """
    async with _cache_lock:
        entry = _cluster_cache.get(cluster_id)
        if entry and entry[1] > datetime.now(timezone.utc):
            return entry[0]
        if entry:
            await entry[0].aclose()

        # Imported cluster: use stored kubeconfig directly (no supervisor needed)
        if namespace == "imported":
            imported_info = _imported_clusters.get(cluster_id)
            if not imported_info:
                raise ValueError(f"Imported cluster {name} not found — please re-import the kubeconfig")
            parsed = _parse_kubeconfig_yaml(imported_info["kubeconfig_yaml"])
            client = _build_client(parsed)
            expires = datetime.now(timezone.utc) + _CLIENT_TTL
            _cluster_cache[cluster_id] = (client, expires)
            _cluster_info[cluster_id] = parsed
            logger.info("Imported cluster client created: %s -> %s", cluster_id, parsed["server"])
            return client

        # Supervisor-managed cluster: fetch kubeconfig secret from supervisor
        supervisor = await get_supervisor_client()
        # Fetch kubeconfig secret: key is "value"
        url = f"/api/v1/namespaces/{namespace}/secrets/{name}-kubeconfig"
        resp = await supervisor.get(url)
        if resp.status_code == 404:
            raise ValueError(f"Kubeconfig secret {name}-kubeconfig not found in {namespace}")
        resp.raise_for_status()

        secret_data = resp.json().get("data", {})
        kc_b64 = secret_data.get("value")
        if not kc_b64:
            raise ValueError(f"Kubeconfig secret {name}-kubeconfig has no 'value' key")

        kc_yaml = base64.b64decode(kc_b64).decode()
        parsed = _parse_kubeconfig_yaml(kc_yaml)
        client = _build_client(parsed)
        expires = datetime.now(timezone.utc) + _CLIENT_TTL
        _cluster_cache[cluster_id] = (client, expires)
        _cluster_info[cluster_id] = parsed
        logger.info("Cluster client created: %s -> %s", cluster_id, parsed["server"])
        return client


def get_cluster_parsed_info(cluster_id: str) -> dict | None:
    """Return the cached parsed kubeconfig dict for a cluster, or None if not yet loaded."""
    return _cluster_info.get(cluster_id)


async def invalidate_cluster(cluster_id: str):
    async with _cache_lock:
        entry = _cluster_cache.pop(cluster_id, None)
        _cluster_info.pop(cluster_id, None)
        if entry:
            await entry[0].aclose()


# ── Imported cluster management ──────────────────────────────────────────────



def _kubeconfig_server(kc_yaml: str) -> str:
    """Extract the server URL from a kubeconfig YAML string."""
    try:
        kc = yaml.safe_load(kc_yaml)
        clusters = kc.get("clusters", [])
        if clusters:
            return clusters[0].get("cluster", {}).get("server", "")
    except Exception:
        pass
    return ""


async def _k8s_api(method: str, path: str, body: dict | None = None) -> dict | None:
    """Make a call to the in-cluster Kubernetes API (for reading/writing secrets)."""
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    host = os.getenv("KUBERNETES_SERVICE_HOST", "")
    port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
    if not host:
        return None
    try:
        token = open(token_path).read().strip()
    except Exception:
        return None
    base = f"https://{host}:{port}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(verify=ca_path, timeout=10.0) as client:
            if method == "GET":
                resp = await client.get(f"{base}{path}", headers=headers)
            elif method == "PUT":
                resp = await client.put(f"{base}{path}", json=body, headers=headers)
            elif method == "POST":
                resp = await client.post(f"{base}{path}", json=body, headers=headers)
            elif method == "PATCH":
                resp = await client.patch(
                    f"{base}{path}", json=body, headers={**headers, "Content-Type": "application/strategic-merge-patch+json"}
                )
            else:
                return None
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.debug("k8s API %s %s failed: %s", method, path, e)
        return None


async def _persist_imported():
    """Write all imported clusters to the vks-imported-configs Secret."""
    data = {
        cluster_id.replace("/", "_"): base64.b64encode(info["kubeconfig_yaml"].encode()).decode()
        for cluster_id, info in _imported_clusters.items()
    }
    # Add metadata to identify names
    meta_json = __import__("json").dumps({
        k.replace("/", "_"): {"name": v["name"], "cluster_id": v["cluster_id"]}
        for k, v in _imported_clusters.items()
    })
    data["_meta"] = base64.b64encode(meta_json.encode()).decode()

    secret_path = f"/api/v1/namespaces/{_K8S_NAMESPACE}/secrets/{_IMPORTED_SECRET_NAME}"
    existing = await _k8s_api("GET", secret_path)
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": _IMPORTED_SECRET_NAME, "namespace": _K8S_NAMESPACE},
        "data": data,
    }
    if existing:
        await _k8s_api("PUT", secret_path, body)
    else:
        await _k8s_api("POST", f"/api/v1/namespaces/{_K8S_NAMESPACE}/secrets", body)


async def load_imported_from_secret():
    """Load imported clusters from the k8s Secret on startup."""
    import json
    secret_path = f"/api/v1/namespaces/{_K8S_NAMESPACE}/secrets/{_IMPORTED_SECRET_NAME}"
    secret = await _k8s_api("GET", secret_path)
    if not secret:
        return
    raw_data = secret.get("data", {})
    meta_b64 = raw_data.pop("_meta", None)
    if not meta_b64:
        return
    try:
        meta = json.loads(base64.b64decode(meta_b64).decode())
    except Exception:
        return
    async with _imported_lock:
        for safe_key, info in meta.items():
            kc_b64 = raw_data.get(safe_key)
            if not kc_b64:
                continue
            try:
                kc_yaml = base64.b64decode(kc_b64).decode()
                cluster_id = info["cluster_id"]
                server = _kubeconfig_server(kc_yaml)
                _imported_clusters[cluster_id] = {
                    "cluster_id": cluster_id,
                    "name": info["name"],
                    "kubeconfig_yaml": kc_yaml,
                    "server": server,
                    "source": "imported",
                }
                logger.info("Loaded imported cluster: %s -> %s", cluster_id, server)
            except Exception as e:
                logger.warning("Failed to load imported cluster %s: %s", safe_key, e)


async def add_imported_cluster(name: str, kubeconfig_yaml: str) -> dict:
    """Validate and store an imported kubeconfig. Returns cluster info."""
    # Validate by parsing
    parsed = _parse_kubeconfig_yaml(kubeconfig_yaml)
    if not parsed["server"]:
        raise ValueError("Could not extract server URL from kubeconfig")

    cluster_id = f"imported/{name}"
    server = parsed["server"]

    async with _imported_lock:
        # Test connectivity
        try:
            test_client = _build_client(parsed)
            resp = await test_client.get("/version", timeout=10.0)
            k8s_version = resp.json().get("gitVersion", "unknown") if resp.status_code == 200 else "unknown"
            await test_client.aclose()
        except Exception as e:
            logger.warning("Imported cluster %s connectivity test failed: %s", name, e)
            k8s_version = "unknown"

        info = {
            "cluster_id": cluster_id,
            "name": name,
            "kubeconfig_yaml": kubeconfig_yaml,
            "server": server,
            "k8s_version": k8s_version,
            "source": "imported",
        }
        _imported_clusters[cluster_id] = info

        # Cache the client immediately
        expires = datetime.now(timezone.utc) + _CLIENT_TTL
        _cluster_cache[cluster_id] = (_build_client(parsed), expires)
        _cluster_info[cluster_id] = parsed

    await _persist_imported()
    return info


async def remove_imported_cluster(cluster_id: str):
    """Remove an imported cluster and invalidate its client."""
    async with _imported_lock:
        _imported_clusters.pop(cluster_id, None)
    await invalidate_cluster(cluster_id)
    await _persist_imported()


async def list_imported_clusters() -> list[dict]:
    async with _imported_lock:
        return list(_imported_clusters.values())


# ── Cluster enumeration ──────────────────────────────────────────────────────

async def list_clusters() -> dict:
    """List all clusters: supervisor-discovered + user-imported.

    Returns {clusters: [...], supervisor_error: str | None}
    Supervisor failure is non-fatal — imported clusters are always returned.
    """
    supervisor_clusters = []
    supervisor_error: str | None = None

    try:
        client = await get_supervisor_client()
        resp = await client.get(
            "/apis/cluster.x-k8s.io/v1beta2/clusters",
            params={"limit": 200},
        )
        if resp.status_code == 401:
            await reset_supervisor_client()
            supervisor_error = "Supervisor token expired — re-authenticate or import a kubeconfig"
        elif not resp.is_success:
            supervisor_error = f"Supervisor returned {resp.status_code}"
        else:
            items = resp.json().get("items", [])
            for item in items:
                meta = item.get("metadata", {})
                status = item.get("status", {})
                spec = item.get("spec", {})
                conditions = status.get("conditions", [])

                def _cond(type_: str) -> bool:
                    return next((c.get("status") == "True" for c in conditions if c.get("type") == type_), False)

                cp_available = _cond("ControlPlaneAvailable")
                workers_available = _cond("WorkersAvailable")
                available = _cond("Available")

                supervisor_clusters.append({
                    "id": f"{meta.get('namespace')}/{meta.get('name')}",
                    "name": meta.get("name", ""),
                    "namespace": meta.get("namespace", ""),
                    "phase": status.get("phase", "Unknown"),
                    "ready": available and cp_available and workers_available,
                    "available": available,
                    "k8s_version": spec.get("topology", {}).get("version", ""),
                    "control_plane_ready": cp_available,
                    "infrastructure_ready": _cond("InfrastructureReady"),
                    "replicas": status.get("replicas") or 0,
                    "ready_replicas": status.get("readyReplicas") or 0,
                    "created_at": meta.get("creationTimestamp", ""),
                    "source": "supervisor",
                })
    except Exception as e:
        supervisor_error = str(e)
        logger.warning("Supervisor unavailable: %s", e)

    # Merge imported clusters (they always survive supervisor outages)
    imported = await list_imported_clusters()
    imported_shaped = []
    for info in imported:
        imported_shaped.append({
            "id": info["cluster_id"],
            "name": info["name"],
            "namespace": "imported",
            "phase": "Imported",
            "ready": True,
            "available": True,
            "k8s_version": info.get("k8s_version", ""),
            "control_plane_ready": True,
            "infrastructure_ready": True,
            "replicas": 0,
            "ready_replicas": 0,
            "created_at": "",
            "source": "imported",
            "server": info.get("server", ""),
        })

    # Dedup imported clusters against supervisor ones by id and name
    supervisor_ids = {c["id"] for c in supervisor_clusters}
    supervisor_names = {c["name"] for c in supervisor_clusters}
    deduped_imported = [
        c for c in imported_shaped
        if c["id"] not in supervisor_ids and c["name"] not in supervisor_names
    ]

    return {
        "clusters": supervisor_clusters + deduped_imported,
        "supervisor_error": supervisor_error,
    }


# ── Generic resource helpers ─────────────────────────────────────────────────

_GROUP_VERSION_MAP = {
    "namespaces":       ("", "v1", "namespaces", False),
    "nodes":            ("", "v1", "nodes", False),
    "pods":             ("", "v1", "pods", True),
    "services":         ("", "v1", "services", True),
    "configmaps":       ("", "v1", "configmaps", True),
    "secrets":          ("", "v1", "secrets", True),
    "pvcs":             ("", "v1", "persistentvolumeclaims", True),
    "events":           ("", "v1", "events", True),
    "resourcequotas":   ("", "v1", "resourcequotas", True),
    "serviceaccounts":  ("", "v1", "serviceaccounts", True),
    "deployments":      ("apps", "v1", "deployments", True),
    "statefulsets":     ("apps", "v1", "statefulsets", True),
    "daemonsets":       ("apps", "v1", "daemonsets", True),
    "replicasets":      ("apps", "v1", "replicasets", True),
    "jobs":             ("batch", "v1", "jobs", True),
    "cronjobs":         ("batch", "v1", "cronjobs", True),
    "ingresses":        ("networking.k8s.io", "v1", "ingresses", True),
    "networkpolicies":  ("networking.k8s.io", "v1", "networkpolicies", True),
    "hpas":             ("autoscaling", "v2", "horizontalpodautoscalers", True),
    "storageclasses":   ("storage.k8s.io", "v1", "storageclasses", False),
    "pvs":              ("", "v1", "persistentvolumes", False),
    "endpoints":        ("", "v1", "endpoints", True),
    "roles":            ("rbac.authorization.k8s.io", "v1", "roles", True),
    "clusterroles":     ("rbac.authorization.k8s.io", "v1", "clusterroles", False),
    "rolebindings":     ("rbac.authorization.k8s.io", "v1", "rolebindings", True),
    "clusterrolebindings": ("rbac.authorization.k8s.io", "v1", "clusterrolebindings", False),
    "poddisruptionbudgets": ("policy", "v1", "poddisruptionbudgets", True),
    "limitranges":          ("", "v1", "limitranges", True),
}

_REDACT_SECRET_KEYS = frozenset({"data", "stringData"})

# Maps k8s Kind (lowercase) → _GROUP_VERSION_MAP key — fixes irregular plurals in kube_apply
_KIND_PLURAL_MAP: dict[str, str] = {
    "namespace": "namespaces",
    "node": "nodes",
    "pod": "pods",
    "service": "services",
    "configmap": "configmaps",
    "secret": "secrets",
    "persistentvolumeclaim": "pvcs",
    "event": "events",
    "resourcequota": "resourcequotas",
    "serviceaccount": "serviceaccounts",
    "deployment": "deployments",
    "statefulset": "statefulsets",
    "daemonset": "daemonsets",
    "replicaset": "replicasets",
    "job": "jobs",
    "cronjob": "cronjobs",
    "ingress": "ingresses",
    "networkpolicy": "networkpolicies",
    "horizontalpodautoscaler": "hpas",
    "storageclass": "storageclasses",
    "persistentvolume": "pvs",
    "endpoints": "endpoints",
    "role": "roles",
    "clusterrole": "clusterroles",
    "rolebinding": "rolebindings",
    "clusterrolebinding": "clusterrolebindings",
    "poddisruptionbudget": "poddisruptionbudgets",
    "limitrange": "limitranges",
}


def _resource_url(kind: str, namespace: str | None = None, name: str | None = None) -> str:
    entry = _GROUP_VERSION_MAP.get(kind)
    if not entry:
        raise ValueError(f"Unknown resource kind: {kind}")
    group, version, plural, namespaced = entry

    if group:
        base = f"/apis/{group}/{version}"
    else:
        base = f"/api/{version}"

    if namespaced and namespace:
        base += f"/namespaces/{namespace}/{plural}"
    else:
        base += f"/{plural}"

    if name:
        base += f"/{name}"
    return base


def _redact_secret(obj: dict) -> dict:
    """Replace secret data values with <redacted> but keep keys visible."""
    result = dict(obj)
    for key in _REDACT_SECRET_KEYS:
        if key in result and isinstance(result[key], dict):
            result[key] = {k: "<redacted>" for k in result[key]}
    return result


async def kube_list(client: httpx.AsyncClient, kind: str, namespace: str | None = None,
                    label_selector: str = "", field_selector: str = "") -> list[dict]:
    url = _resource_url(kind, namespace)
    params: dict = {"limit": 500}
    if label_selector:
        params["labelSelector"] = label_selector
    if field_selector:
        params["fieldSelector"] = field_selector
    resp = await client.get(url, params=params)
    if resp.status_code == 404:
        return []
    _check_k8s_status(resp, "list", kind, namespace or "")
    items = resp.json().get("items", [])
    if kind == "secrets":
        items = [_redact_secret(i) for i in items]
    return items


async def kube_get(client: httpx.AsyncClient, kind: str, name: str,
                   namespace: str | None = None) -> dict:
    url = _resource_url(kind, namespace, name)
    resp = await client.get(url)
    _check_k8s_status(resp, "get", kind, namespace or "")
    obj = resp.json()
    if kind == "secrets":
        obj = _redact_secret(obj)
    return obj


async def kube_patch(client: httpx.AsyncClient, kind: str, name: str,
                     namespace: str, patch: dict) -> dict:
    url = _resource_url(kind, namespace, name)
    resp = await client.patch(
        url, json=patch,
        headers={"Content-Type": "application/strategic-merge-patch+json"},
    )
    _check_k8s_status(resp, "patch", kind, namespace)
    return resp.json()


async def kube_delete(client: httpx.AsyncClient, kind: str, name: str,
                      namespace: str) -> dict:
    url = _resource_url(kind, namespace, name)
    resp = await client.delete(url)
    _check_k8s_status(resp, "delete", kind, namespace)
    return resp.json()


async def kube_apply(client: httpx.AsyncClient, manifest: dict) -> dict:
    """Server-side apply a manifest."""
    kind_lower = manifest.get("kind", "").lower()
    kind = _KIND_PLURAL_MAP.get(kind_lower, kind_lower + "s")
    metadata = manifest.get("metadata", {})
    namespace = metadata.get("namespace")
    name = metadata.get("name")
    url = _resource_url(kind, namespace, name)
    resp = await client.patch(
        url, json=manifest,
        params={"fieldManager": "vks-broker", "force": "true"},
        headers={"Content-Type": "application/apply-patch+yaml"},
    )
    if resp.status_code == 404:
        create_url = _resource_url(kind, namespace)
        resp = await client.post(create_url, json=manifest)
    _check_k8s_status(resp, "apply", kind, namespace or "")
    return resp.json()


async def kube_logs(client: httpx.AsyncClient, namespace: str, pod: str,
                    container: str = "", tail_lines: int = 100,
                    follow: bool = False) -> httpx.Response:
    """Return a streaming response for pod logs."""
    params: dict = {"tailLines": tail_lines}
    if container:
        params["container"] = container
    if follow:
        params["follow"] = "true"
    url = f"/api/v1/namespaces/{namespace}/pods/{pod}/log"
    return await client.send(
        client.build_request("GET", url, params=params),
        stream=follow,
    )
