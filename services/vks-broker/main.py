"""
VKS Broker — FastAPI service providing the VKS Console backend.
Phases A (read), B (actions), C (create), D (AI).
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime as _datetime
import yaml as _yaml
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, HTTPException, Header, Query, Request, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import StreamingResponse, JSONResponse

from contextlib import asynccontextmanager

from broker import (
    get_cluster_client, get_cluster_parsed_info, get_supervisor_client, reset_supervisor_client,
    invalidate_cluster, kube_apply, kube_delete, kube_get, kube_list, kube_logs, kube_patch,
    list_clusters, add_imported_cluster, remove_imported_cluster,
    list_imported_clusters, load_imported_from_secret, KubeForbiddenError,
)
from confirm import consume_token, issue_token
from audit import emit as audit_emit, get_recent as audit_get_recent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vks-broker")

LLM_GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://llm-gateway:8008")


_SUPERVISOR_RESET_INTERVAL = int(os.getenv("SUPERVISOR_TOKEN_RESET_INTERVAL", "2400"))  # 40 min


async def _supervisor_token_reset_loop():
    """Proactively reset the supervisor client before the WCP session idle-expires.
    The token-refresher CronJob writes a fresh token every 45 min; we reset the
    in-memory client every 40 min so it always re-reads the latest token from the
    mounted secret — no 401 required to trigger the refresh."""
    await asyncio.sleep(_SUPERVISOR_RESET_INTERVAL)
    while True:
        try:
            await reset_supervisor_client()
            logger.info("Supervisor client proactively reset — will re-read token on next request")
        except Exception as e:
            logger.warning("Supervisor client reset failed: %s", e)
        await asyncio.sleep(_SUPERVISOR_RESET_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await load_imported_from_secret()
    logger.info("Imported clusters loaded from secret")
    reset_task = asyncio.create_task(_supervisor_token_reset_loop())
    yield
    reset_task.cancel()
    try:
        await reset_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="VKS Broker", version="1.0.0", lifespan=lifespan)

# ── Rate limiting (token bucket, per IP) ─────────────────────────────────────

_rate_buckets: dict[str, tuple[float, float]] = {}  # ip → (tokens, last_refill_time)
_RATE_LIMIT = int(os.getenv("RATE_LIMIT_RPS", "30"))   # requests/sec per IP
_RATE_BURST = int(os.getenv("RATE_LIMIT_BURST", "60"))  # burst capacity


def _check_rate(ip: str) -> bool:
    now = time.monotonic()
    tokens, last = _rate_buckets.get(ip, (_RATE_BURST, now))
    elapsed = now - last
    tokens = min(_RATE_BURST, tokens + elapsed * _RATE_LIMIT)
    if tokens < 1:
        return False
    _rate_buckets[ip] = (tokens - 1, now)
    return True


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Key on the authenticated user, not the proxy IP (all traffic arrives from one gateway IP)
    key = (
        request.headers.get("x-forwarded-user")
        or request.headers.get("x-forwarded-email")
        or (request.client.host if request.client else "unknown")
    )
    if key != "testclient" and not _check_rate(key):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
    return await call_next(request)


@app.exception_handler(KubeForbiddenError)
async def kube_forbidden_handler(request: Request, exc: KubeForbiddenError):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=403,
        content={
            "error_type": "forbidden",
            "verb": exc.verb,
            "resource": exc.resource,
            "namespace": exc.namespace,
            "detail": str(exc),
        },
    )


def _consume(token: str) -> dict:
    """Consume a confirm token, raising HTTP 400 if invalid/expired/reused."""
    try:
        return consume_token(token)
    except ValueError as e:
        raise HTTPException(400, f"Invalid confirm token: {e}")


def _user(request: Request) -> str:
    return (
        request.headers.get("x-forwarded-preferred-username")
        or request.headers.get("x-forwarded-user")
        or request.headers.get("x-forwarded-email")
        or "anonymous"
    )


async def _cluster(cluster_id: str) -> httpx.AsyncClient:
    """Parse cluster_id = 'namespace/name', get client, 401 → refresh."""
    try:
        ns, name = cluster_id.split("/", 1)
    except ValueError:
        raise HTTPException(400, f"cluster_id must be 'namespace/name', got: {cluster_id}")
    try:
        return await get_cluster_client(cluster_id, ns, name)
    except Exception as e:
        raise HTTPException(503, f"Cannot connect to cluster {cluster_id}: {e}")


# ── Health & Metrics ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "vks-broker"}


@app.get("/healthz")
async def healthz():
    """Deep health check: redis + supervisor connectivity."""
    checks: dict[str, bool] = {}

    # Redis check
    redis_url = os.getenv("REDIS_URL", "")
    if redis_url:
        try:
            import redis.asyncio as _aioredis
            _rc = _aioredis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
            await asyncio.wait_for(_rc.ping(), timeout=2.0)
            await _rc.aclose()
            checks["redis"] = True
        except Exception:
            checks["redis"] = False
    else:
        checks["redis"] = None  # not configured — not a failure

    # Supervisor / clusters API check
    try:
        sup = await asyncio.wait_for(get_supervisor_client(), timeout=2.0)
        resp = await asyncio.wait_for(sup.get("/vcf/lcm/nsxt/v1/vcenters"), timeout=2.0)
        checks["clusters_api"] = resp.status_code < 500
    except Exception:
        checks["clusters_api"] = False

    failed = any(v is False for v in checks.values())
    status_code = 503 if failed else 200
    body = {"status": "degraded" if failed else "ok", "checks": checks}
    return JSONResponse(content=body, status_code=status_code)


@app.get("/audit")
async def audit_log(limit: int = 50):
    """Return recent broker audit events (in-memory, most recent first)."""
    return {"events": audit_get_recent(min(limit, 200))}


_request_count: dict[str, int] = {}
_request_errors: dict[str, int] = {}

def _track(path: str, ok: bool):
    _request_count[path] = _request_count.get(path, 0) + 1
    if not ok:
        _request_errors[path] = _request_errors.get(path, 0) + 1


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    resp = await call_next(request)
    # Use the route template (e.g. /clusters/{cluster_id:path}/pods) not the concrete path,
    # to avoid unbounded label cardinality in the metrics counters
    route = request.scope.get("route")
    path = route.path if route else request.url.path
    _track(path, resp.status_code < 500)
    return resp


@app.get("/metrics", include_in_schema=False)
async def metrics():
    from fastapi.responses import PlainTextResponse
    lines: list[str] = [
        "# HELP vks_broker_requests_total Total HTTP requests by path",
        "# TYPE vks_broker_requests_total counter",
    ]
    for path, count in _request_count.items():
        safe = path.replace('"', '')
        lines.append(f'vks_broker_requests_total{{path="{safe}"}} {count}')
    lines += [
        "# HELP vks_broker_errors_total Total 5xx responses by path",
        "# TYPE vks_broker_errors_total counter",
    ]
    for path, count in _request_errors.items():
        safe = path.replace('"', '')
        lines.append(f'vks_broker_errors_total{{path="{safe}"}} {count}')
    from broker import _imported_clusters
    lines += [
        "# HELP vks_broker_imported_clusters Number of imported clusters",
        "# TYPE vks_broker_imported_clusters gauge",
        f"vks_broker_imported_clusters {len(_imported_clusters)}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


# ── Phase A: Cluster discovery ────────────────────────────────────────────────

@app.get("/clusters")
async def get_clusters():
    # Never raises — supervisor failure is returned as supervisor_error, not 503
    result = await list_clusters()
    return result


@app.post("/supervisor/reconnect")
async def supervisor_reconnect(request: Request):
    """Force a WCP re-login using credentials stored in K8s secrets/configmap.
    Reads VSPHERE_HOST, VSPHERE_USERNAME from env (injected from mco-config) and
    VSPHERE_PASSWORD from the vsphere-credentials secret via the in-cluster API.
    No credentials are accepted from or returned to the caller."""
    import base64 as _b64
    import httpx as _httpx

    host = os.getenv("VSPHERE_SUPERVISOR_HOST", "").strip()
    username = os.getenv("VSPHERE_SUPERVISOR_USERNAME", "").strip()
    if not host or not username:
        raise HTTPException(503, "VSPHERE_SUPERVISOR_HOST / VSPHERE_SUPERVISOR_USERNAME not configured")

    # Read password from vsphere-credentials secret via in-cluster API
    ns_file = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
    ns = open(ns_file).read().strip() if os.path.exists(ns_file) else os.getenv("POD_NAMESPACE", "vcf-ai-ops")
    secret = await _k8s_api("GET", f"/api/v1/namespaces/{ns}/secrets/vsphere-credentials")
    if not secret:
        raise HTTPException(503, "vsphere-credentials secret not found or not accessible")
    password_b64 = secret.get("data", {}).get("password", "")
    if not password_b64:
        raise HTTPException(503, "vsphere-credentials secret has no 'password' key")
    password = _b64.b64decode(password_b64).decode()

    # WCP login
    try:
        async with _httpx.AsyncClient(verify=False, timeout=20.0) as client:
            resp = await client.post(
                f"https://{host}/wcp/login",
                auth=(username, password),
            )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("session_id") or data.get("token")
        if not token:
            raise HTTPException(502, f"WCP login returned no token. Keys: {list(data.keys())}")
    except _httpx.HTTPStatusError as e:
        raise HTTPException(502, f"WCP login failed: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(502, f"WCP login error: {e}")

    # Patch kubectl-config secret with fresh token
    kc_secret = await _k8s_api("GET", f"/api/v1/namespaces/{ns}/secrets/kubectl-config")
    if not kc_secret:
        raise HTTPException(503, "kubectl-config secret not found")
    import yaml as _yaml2, base64 as _b64b
    raw = _b64b.b64decode(kc_secret["data"]["config"]).decode()
    kube_cfg = _yaml2.safe_load(raw)
    user_key = f"wcp:{host}:{username}"
    updated = False
    for u in kube_cfg.get("users", []):
        if u.get("name") == user_key:
            u["user"]["token"] = token
            updated = True
            break
    if not updated:
        raise HTTPException(500, f"User {user_key!r} not found in kubectl-config kubeconfig")
    new_b64 = _b64b.b64encode(_yaml2.dump(kube_cfg, default_flow_style=False).encode()).decode()
    patch = {"data": {"config": new_b64}}
    await _k8s_api("PATCH", f"/api/v1/namespaces/{ns}/secrets/kubectl-config", patch)

    # Reset in-memory client so next request picks up the new token
    await reset_supervisor_client()
    user = _user(request)
    logger.info("Supervisor reconnected by %s", user)
    return {"ok": True, "message": "Supervisor reconnected — new WCP token active"}


@app.post("/clusters/import")
async def import_cluster(request: Request):
    """Import a cluster by pasting its kubeconfig. Works independently of the supervisor."""
    body = await request.json()
    name = (body.get("name") or "").strip()
    kubeconfig_yaml = (body.get("kubeconfig_yaml") or body.get("kubeconfig") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    if not kubeconfig_yaml:
        raise HTTPException(400, "kubeconfig_yaml required")
    user = _user(request)
    await audit_emit(user, "import-cluster", f"imported/{name}", "imported", "Cluster", name)
    try:
        info = await add_imported_cluster(name, kubeconfig_yaml)
        return {"ok": True, "cluster": info}
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"Import failed: {e}")


@app.delete("/clusters/import/{name}")
async def delete_imported_cluster(name: str, request: Request):
    """Remove an imported cluster."""
    cluster_id = f"imported/{name}"
    user = _user(request)
    await audit_emit(user, "remove-imported-cluster", cluster_id, "imported", "Cluster", name)
    await remove_imported_cluster(cluster_id)
    return {"ok": True}


@app.get("/clusters/{cluster_id:path}/overview")
async def cluster_overview(cluster_id: str):
    client = await _cluster(cluster_id)
    ns_list, nodes, deps, sts, dsets, pods = await asyncio.gather(
        kube_list(client, "namespaces"),
        kube_list(client, "nodes"),
        kube_list(client, "deployments"),
        kube_list(client, "statefulsets"),
        kube_list(client, "daemonsets"),
        kube_list(client, "pods"),
        return_exceptions=True,
    )

    def _safe(v):
        return v if not isinstance(v, Exception) else []

    ns_list, nodes, deps, sts, dsets, pods = (_safe(x) for x in [ns_list, nodes, deps, sts, dsets, pods])

    # Node capacity summary
    allocatable_cpu = 0.0
    allocatable_mem_mib = 0
    used_cpu = 0.0
    used_mem_mib = 0
    node_ready = 0
    for n in nodes:
        conds = n.get("status", {}).get("conditions", [])
        if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds):
            node_ready += 1
        alloc = n.get("status", {}).get("allocatable", {})
        allocatable_cpu += _cpu_to_m(alloc.get("cpu", "0")) / 1000
        allocatable_mem_mib += _mem_to_mib(alloc.get("memory", "0"))

    pod_running = sum(1 for p in pods if p.get("status", {}).get("phase") == "Running")
    pod_pending = sum(1 for p in pods if p.get("status", {}).get("phase") == "Pending")
    pod_failed = sum(1 for p in pods if p.get("status", {}).get("phase") == "Failed")

    def _is_crashloop(pod: dict) -> bool:
        for cs in pod.get("status", {}).get("containerStatuses", []):
            state = cs.get("state", {})
            if state.get("waiting", {}).get("reason") == "CrashLoopBackOff":
                return True
            if cs.get("restartCount", 0) >= 5:
                return True
        return False

    crashloop_pods = [
        {"name": p.get("metadata", {}).get("name", ""), "namespace": p.get("metadata", {}).get("namespace", "")}
        for p in pods if _is_crashloop(p)
    ]

    # Fetch recent events
    try:
        events_raw = await kube_list(client, "events")
        events_raw.sort(key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "", reverse=True)
        recent_events = [_format_event(e) for e in events_raw[:20]]
    except Exception:
        recent_events = []

    user_ns = [n for n in ns_list if not n.get("metadata", {}).get("name", "").startswith(("kube-", "cert-manager", "capi"))]

    # Degraded deployments: desired > 0 but ready < desired
    def _is_degraded_dep(d: dict) -> bool:
        spec = d.get("spec", {})
        status = d.get("status", {})
        desired = spec.get("replicas", 0) or 0
        ready = status.get("readyReplicas", 0) or 0
        return desired > 0 and ready < desired

    degraded_deps = [
        {"name": d.get("metadata", {}).get("name", ""), "namespace": d.get("metadata", {}).get("namespace", ""),
         "ready": d.get("status", {}).get("readyReplicas", 0) or 0, "desired": d.get("spec", {}).get("replicas", 0) or 0}
        for d in deps if _is_degraded_dep(d)
    ]

    return {
        "nodes": {"total": len(nodes), "ready": node_ready},
        "allocatable": {"cpu_cores": round(allocatable_cpu, 1), "memory_mib": allocatable_mem_mib},
        "workloads": {
            "deployments": len(deps),
            "statefulsets": len(sts),
            "daemonsets": len(dsets),
        },
        "pods": {"running": pod_running, "pending": pod_pending, "failed": pod_failed, "total": len(pods)},
        "namespaces": len(user_ns),
        "recent_events": recent_events,
        "degraded_deployments": degraded_deps,
        "crashloop_pods": crashloop_pods,
    }


@app.get("/clusters/{cluster_id:path}/namespaces")
async def list_namespaces(cluster_id: str):
    client = await _cluster(cluster_id)
    ns_list = await kube_list(client, "namespaces")

    async def _ns_detail(ns: dict) -> dict:
        name = ns.get("metadata", {}).get("name", "")
        phase = ns.get("status", {}).get("phase", "")
        is_system = name.startswith(("kube-", "cert-manager", "capi", "tanzu", "vmware", "tkg"))
        pods_r, quotas_r = await asyncio.gather(
            kube_list(client, "pods", name),
            kube_list(client, "resourcequotas", name),
            return_exceptions=True,
        )
        pods = pods_r if not isinstance(pods_r, Exception) else []
        pod_count = len(pods)
        quotas = quotas_r if not isinstance(quotas_r, Exception) else []
        req_cpu_m = 0.0
        req_mem_mib = 0.0
        for pod in pods:
            for c in pod.get("spec", {}).get("containers", []):
                req = c.get("resources", {}).get("requests", {})
                req_cpu_m += _cpu_to_m(req.get("cpu", "0"))
                req_mem_mib += _mem_to_mib(req.get("memory", "0"))
        return {
            "name": name, "phase": phase, "is_system": is_system,
            "pod_count": pod_count,
            "req_cpu_m": round(req_cpu_m),
            "req_mem_mib": round(req_mem_mib),
            "quotas": [_format_quota(q) for q in quotas],
        }

    results = await asyncio.gather(*(_ns_detail(ns) for ns in ns_list), return_exceptions=True)
    return {"namespaces": [r for r in results if not isinstance(r, Exception)]}


@app.get("/clusters/{cluster_id:path}/nodes")
async def list_nodes(cluster_id: str):
    client = await _cluster(cluster_id)
    nodes = await kube_list(client, "nodes")
    return {"nodes": [_format_node(n) for n in nodes]}


@app.get("/clusters/{cluster_id:path}/workloads")
async def list_workloads(cluster_id: str, namespace: str = "", kind: str = "deployments"):
    client = await _cluster(cluster_id)
    allowed = {"deployments", "statefulsets", "daemonsets", "jobs", "cronjobs", "replicasets"}
    if kind not in allowed:
        raise HTTPException(400, f"kind must be one of {sorted(allowed)}")
    items = await kube_list(client, kind, namespace or None)
    return {"kind": kind, "items": [_format_workload(i, kind) for i in items]}


@app.get("/clusters/{cluster_id:path}/pods")
async def list_pods(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    pods = await kube_list(client, "pods", namespace or None)
    return {"pods": [_format_pod(p) for p in pods]}


@app.get("/clusters/{cluster_id:path}/pods/detail")
async def pod_detail(cluster_id: str, name: str, namespace: str = "default"):
    client = await _cluster(cluster_id)
    resp = await client.get(f"/api/v1/namespaces/{namespace}/pods/{name}")
    if resp.status_code == 404:
        raise HTTPException(404, f"Pod {name} not found in {namespace}")
    resp.raise_for_status()
    pod = resp.json()
    spec = pod.get("spec", {})
    status = pod.get("status", {})

    def _fmt_env(container: dict) -> list:
        result = []
        for e in container.get("env", []):
            vf = e.get("valueFrom", {})
            if "secretKeyRef" in vf:
                ref = vf["secretKeyRef"]
                result.append({"name": e["name"], "value": "••••", "source": f"secret:{ref.get('name')}/{ref.get('key')}"})
            elif "configMapKeyRef" in vf:
                ref = vf["configMapKeyRef"]
                result.append({"name": e["name"], "value": None, "source": f"configmap:{ref.get('name')}/{ref.get('key')}"})
            elif "fieldRef" in vf:
                result.append({"name": e["name"], "value": None, "source": f"field:{vf['fieldRef'].get('fieldPath')}"})
            else:
                result.append({"name": e["name"], "value": e.get("value", ""), "source": None})
        return result

    def _fmt_probe(probe: dict | None) -> dict | None:
        if not probe:
            return None
        result: dict = {
            "initial_delay": probe.get("initialDelaySeconds", 0),
            "period": probe.get("periodSeconds", 10),
            "failure_threshold": probe.get("failureThreshold", 3),
        }
        if "httpGet" in probe:
            h = probe["httpGet"]
            result["type"] = "httpGet"
            result["path"] = h.get("path", "/")
            result["port"] = h.get("port")
        elif "exec" in probe:
            result["type"] = "exec"
            result["command"] = probe["exec"].get("command", [])
        elif "tcpSocket" in probe:
            result["type"] = "tcpSocket"
            result["port"] = probe["tcpSocket"].get("port")
        else:
            result["type"] = "unknown"
        return result

    containers_detail = []
    for c in spec.get("containers", []):
        cstatus = next((cs for cs in status.get("containerStatuses", []) if cs.get("name") == c.get("name")), {})
        containers_detail.append({
            "name": c.get("name", ""),
            "image": c.get("image", ""),
            "ready": cstatus.get("ready", False),
            "state": _container_state(cstatus) if cstatus else "unknown",
            "restarts": cstatus.get("restartCount", 0),
            "env": _fmt_env(c),
            "volume_mounts": [
                {"name": vm.get("name", ""), "mount_path": vm.get("mountPath", ""), "read_only": vm.get("readOnly", False)}
                for vm in c.get("volumeMounts", [])
            ],
            "liveness_probe": _fmt_probe(c.get("livenessProbe")),
            "readiness_probe": _fmt_probe(c.get("readinessProbe")),
            "resources": {
                "req_cpu": c.get("resources", {}).get("requests", {}).get("cpu", ""),
                "req_mem": c.get("resources", {}).get("requests", {}).get("memory", ""),
                "lim_cpu": c.get("resources", {}).get("limits", {}).get("cpu", ""),
                "lim_mem": c.get("resources", {}).get("limits", {}).get("memory", ""),
            },
        })

    return {
        "name": pod.get("metadata", {}).get("name", ""),
        "namespace": namespace,
        "phase": status.get("phase", ""),
        "node": spec.get("nodeName", ""),
        "containers": containers_detail,
        "volumes": [
            {"name": v.get("name", ""), "type": next(iter(k for k in v if k != "name"), "unknown")}
            for v in spec.get("volumes", [])
        ],
        "conditions": [
            {"type": c.get("type", ""), "status": c.get("status", ""), "reason": c.get("reason", ""), "message": c.get("message", "")}
            for c in status.get("conditions", [])
        ],
    }


@app.get("/clusters/{cluster_id:path}/pods/metrics")
async def pod_metrics(cluster_id: str, namespace: str = ""):
    """Return live CPU/memory from metrics-server. Returns available=False when metrics-server absent."""
    client = await _cluster(cluster_id)
    if namespace:
        url = f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods"
    else:
        url = "/apis/metrics.k8s.io/v1beta1/pods"
    try:
        resp = await client.get(url)
        if resp.status_code in (404, 503, 501):
            return {"available": False, "pods": []}
        resp.raise_for_status()
        items = resp.json().get("items", [])
        pods = []
        for item in items:
            meta = item.get("metadata", {})
            containers = item.get("containers", [])
            cpu_m = sum(_cpu_to_m(c.get("usage", {}).get("cpu", "0")) for c in containers)
            mem_mib = sum(_mem_to_mib(c.get("usage", {}).get("memory", "0")) for c in containers)
            pods.append({
                "name": meta.get("name", ""),
                "namespace": meta.get("namespace", ""),
                "cpu_m": round(cpu_m),
                "mem_mib": round(mem_mib),
            })
        return {"available": True, "pods": pods}
    except Exception:
        return {"available": False, "pods": []}


@app.get("/clusters/{cluster_id:path}/pod-resources")
async def pod_resources(cluster_id: str, namespace: str = ""):
    """Merge pod spec resources (requests/limits) with live metrics usage."""
    client = await _cluster(cluster_id)
    pods_r, metrics_r = await asyncio.gather(
        kube_list(client, "pods", namespace or None),
        pod_metrics(cluster_id, namespace),  # reuse existing endpoint logic
        return_exceptions=True,
    )
    pods = pods_r if not isinstance(pods_r, Exception) else []

    # Build metrics lookup
    metrics_by_pod: dict[str, dict] = {}
    if not isinstance(metrics_r, Exception) and isinstance(metrics_r, dict) and metrics_r.get("available"):
        for pm in metrics_r.get("pods", []):
            key = f"{pm['namespace']}/{pm['name']}"
            metrics_by_pod[key] = pm

    result = []
    for pod in pods:
        meta = pod.get("metadata", {})
        name = meta.get("name", "")
        ns = meta.get("namespace", "")
        phase = pod.get("status", {}).get("phase", "Unknown")
        containers = pod.get("spec", {}).get("containers", [])
        req_cpu_m = 0.0
        req_mem_mib = 0
        lim_cpu_m = 0.0
        lim_mem_mib = 0
        for c in containers:
            res = c.get("resources", {})
            req_cpu_m += _cpu_to_m(res.get("requests", {}).get("cpu", "0"))
            req_mem_mib += _mem_to_mib(res.get("requests", {}).get("memory", "0"))
            lim_cpu_m += _cpu_to_m(res.get("limits", {}).get("cpu", "0"))
            lim_mem_mib += _mem_to_mib(res.get("limits", {}).get("memory", "0"))

        key = f"{ns}/{name}"
        live = metrics_by_pod.get(key, {})
        live_cpu_m = live.get("cpu_m", None)
        live_mem_mib = live.get("mem_mib", None)

        result.append({
            "name": name,
            "namespace": ns,
            "phase": phase,
            "req_cpu_m": round(req_cpu_m),
            "req_mem_mib": round(req_mem_mib),
            "lim_cpu_m": round(lim_cpu_m),
            "lim_mem_mib": round(lim_mem_mib),
            "live_cpu_m": round(live_cpu_m) if live_cpu_m is not None else None,
            "live_mem_mib": round(live_mem_mib) if live_mem_mib is not None else None,
            "cpu_pct": round(live_cpu_m / lim_cpu_m * 100) if (live_cpu_m is not None and lim_cpu_m > 0) else None,
            "mem_pct": round(live_mem_mib / lim_mem_mib * 100) if (live_mem_mib is not None and lim_mem_mib > 0) else None,
        })

    result.sort(key=lambda x: x.get("live_cpu_m") or 0, reverse=True)
    return {"pods": result, "metrics_available": bool(metrics_by_pod), "total": len(result)}


# ── Cost Estimator (Loop 44) ─────────────────────────────────────────────────

# Default pricing: typical cloud on-demand rates (USD)
_DEFAULT_CPU_HOUR = 0.048   # per vCPU-hour
_DEFAULT_MEM_HOUR = 0.006   # per GiB-hour

HOURS_PER_MONTH = 730.0


@app.get("/clusters/{cluster_id:path}/cost-estimate")
async def cost_estimate(
    cluster_id: str,
    namespace: str = "",
    cpu_hour: float = _DEFAULT_CPU_HOUR,
    mem_hour: float = _DEFAULT_MEM_HOUR,
):
    """
    Estimate monthly cost per namespace and per workload based on pod CPU/memory
    requests. Pricing is configurable (defaults: $0.048/vCPU-hr, $0.006/GiB-hr).
    Only Running pods are included.
    """
    client = await _cluster(cluster_id)
    pods = await kube_list(client, "pods", namespace or None)

    # Per-namespace aggregation
    ns_data: dict[str, dict] = {}
    # Per-pod list for top-N
    pod_costs: list[dict] = []

    for pod in pods:
        meta = pod.get("metadata", {})
        name = meta.get("name", "")
        ns = meta.get("namespace", "")
        phase = pod.get("status", {}).get("phase", "")
        if phase != "Running":
            continue

        containers = pod.get("spec", {}).get("containers", [])
        req_cpu_m = sum(_cpu_to_m(c.get("resources", {}).get("requests", {}).get("cpu", "0"))
                        for c in containers)
        req_mem_mib = sum(_mem_to_mib(c.get("resources", {}).get("requests", {}).get("memory", "0"))
                          for c in containers)

        cpu_cores = req_cpu_m / 1000.0
        mem_gib = req_mem_mib / 1024.0
        hourly = cpu_cores * cpu_hour + mem_gib * mem_hour
        monthly = hourly * HOURS_PER_MONTH

        pod_costs.append({
            "name": name,
            "namespace": ns,
            "cpu_cores": round(cpu_cores, 3),
            "mem_gib": round(mem_gib, 3),
            "hourly": round(hourly, 4),
            "monthly": round(monthly, 2),
        })

        if ns not in ns_data:
            ns_data[ns] = {"namespace": ns, "cpu_cores": 0.0, "mem_gib": 0.0, "hourly": 0.0, "monthly": 0.0, "pod_count": 0}
        ns_data[ns]["cpu_cores"] = round(ns_data[ns]["cpu_cores"] + cpu_cores, 3)
        ns_data[ns]["mem_gib"] = round(ns_data[ns]["mem_gib"] + mem_gib, 3)
        ns_data[ns]["hourly"] = round(ns_data[ns]["hourly"] + hourly, 4)
        ns_data[ns]["monthly"] = round(ns_data[ns]["monthly"] + monthly, 2)
        ns_data[ns]["pod_count"] += 1

    total_hourly = sum(n["hourly"] for n in ns_data.values())
    total_monthly = sum(n["monthly"] for n in ns_data.values())
    total_cpu = sum(n["cpu_cores"] for n in ns_data.values())
    total_mem = sum(n["mem_gib"] for n in ns_data.values())

    ns_list = sorted(ns_data.values(), key=lambda x: x["monthly"], reverse=True)
    pod_costs.sort(key=lambda x: x["monthly"], reverse=True)

    return {
        "total": {
            "hourly": round(total_hourly, 4),
            "monthly": round(total_monthly, 2),
            "cpu_cores": round(total_cpu, 3),
            "mem_gib": round(total_mem, 3),
        },
        "namespaces": ns_list,
        "top_pods": pod_costs[:20],
        "pricing": {"cpu_hour": cpu_hour, "mem_hour": mem_hour},
    }


# ── Multi-Cluster Fleet Health (Loop 49) ─────────────────────────────────────

@app.get("/fleet/k8s-health")
async def fleet_k8s_health():
    """
    Fetch health summaries for ALL registered K8s clusters in parallel.
    Unreachable clusters are included with status='unreachable' rather than raising.
    """
    clusters_data = await list_clusters()
    clusters_list = clusters_data.get("clusters", [])

    async def _fetch_one(cluster: dict) -> dict:
        cid = cluster.get("id") or cluster.get("cluster_id", "")
        name = cluster.get("name", "")
        base = {
            "cluster_id": cid,
            "name": name,
            "status": "unknown",
            "provider": cluster.get("provider", ""),
            "version": "",
            "nodes": {"total": 0, "ready": 0},
            "pods": {"total": 0},
            "workloads": {"total": 0, "healthy": 0},
            "namespaces": 0,
            "capacity": {"cpu_cores": 0.0, "memory_gib": 0.0},
        }
        try:
            client = await asyncio.wait_for(_cluster(cid), timeout=5.0)
            # Fetch summary
            version_resp, nodes_raw, pods_raw, deploy_raw, sts_raw, ns_raw = await asyncio.gather(
                client.get("/version"),
                kube_list(client, "nodes"),
                kube_list(client, "pods"),
                kube_list(client, "deployments"),
                kube_list(client, "statefulsets"),
                kube_list(client, "namespaces"),
                return_exceptions=True,
            )
            if not isinstance(version_resp, Exception) and version_resp.status_code == 200:
                v = version_resp.json()
                base["version"] = f"{v.get('major', '')}.{v.get('minor', '')}"

            nodes = nodes_raw if not isinstance(nodes_raw, Exception) else []
            ready = sum(1 for n in nodes if any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in n.get("status", {}).get("conditions", [])
            ))
            base["nodes"] = {"total": len(nodes), "ready": ready}

            pods = pods_raw if not isinstance(pods_raw, Exception) else []
            base["pods"] = {"total": len(pods)}

            deploys = (deploy_raw if not isinstance(deploy_raw, Exception) else []) + \
                      (sts_raw if not isinstance(sts_raw, Exception) else [])
            healthy = sum(
                1 for d in deploys
                if (d.get("status", {}).get("readyReplicas") or 0) >= (d.get("spec", {}).get("replicas") or 1)
            )
            base["workloads"] = {"total": len(deploys), "healthy": healthy}

            ns_list = ns_raw if not isinstance(ns_raw, Exception) else []
            base["namespaces"] = len(ns_list)

            cpu_total = sum(_cpu_to_m(n.get("status", {}).get("allocatable", {}).get("cpu", "0")) / 1000
                            for n in nodes)
            mem_total = sum(_mem_to_mib(n.get("status", {}).get("allocatable", {}).get("memory", "0")) / 1024
                            for n in nodes)
            base["capacity"] = {"cpu_cores": round(cpu_total, 2), "memory_gib": round(mem_total, 2)}

            if ready < len(nodes):
                base["status"] = "degraded"
            elif healthy < len(deploys):
                base["status"] = "degraded"
            else:
                base["status"] = "healthy"
        except Exception:
            base["status"] = "unreachable"
        return base

    health_results = await asyncio.gather(
        *[_fetch_one(c) for c in clusters_list],
        return_exceptions=True,
    )

    summaries = []
    for i, r in enumerate(health_results):
        if isinstance(r, Exception):
            c = clusters_list[i]
            summaries.append({
                "cluster_id": c.get("cluster_id", ""),
                "name": c.get("name", ""),
                "status": "unreachable",
                "version": "", "nodes": {"total": 0, "ready": 0},
                "pods": {"total": 0}, "workloads": {"total": 0, "healthy": 0},
                "namespaces": 0, "capacity": {"cpu_cores": 0.0, "memory_gib": 0.0},
            })
        else:
            summaries.append(r)

    healthy_count = sum(1 for s in summaries if s["status"] == "healthy")
    degraded_count = sum(1 for s in summaries if s["status"] == "degraded")
    unreachable_count = sum(1 for s in summaries if s["status"] == "unreachable")

    return {
        "clusters": summaries,
        "total": len(summaries),
        "healthy": healthy_count,
        "degraded": degraded_count,
        "unreachable": unreachable_count,
        "last_updated": _datetime.utcnow().isoformat(),
    }


# ── Orphan Resource Detector (Loop 48) ───────────────────────────────────────

def _selector_matches_labels(selector: dict, labels: dict) -> bool:
    """Return True if all selector key=value pairs are in labels."""
    if not selector:
        return False  # empty/headless services intentionally have no selector
    return all(labels.get(k) == v for k, v in selector.items())


@app.get("/clusters/{cluster_id:path}/orphans")
async def detect_orphans(cluster_id: str, namespace: str = ""):
    """
    Detect orphaned resources:
    - Services whose selector matches no Running pod
    - PVCs not mounted by any pod (Released or no pod references)
    - Ingress rules pointing to non-existent Services
    - Deployments scaled to 0 (not by HPA or intentional annotation)
    """
    client = await _cluster(cluster_id)
    ns = namespace or None

    # Fetch everything in parallel
    (pods_r, services_r, pvcs_r, ingresses_r, deployments_r, hpas_r) = await asyncio.gather(
        kube_list(client, "pods", ns),
        kube_list(client, "services", ns),
        kube_list(client, "pvcs", ns),
        kube_list(client, "ingresses", ns),
        kube_list(client, "deployments", ns),
        kube_list(client, "hpas", ns),
        return_exceptions=True,
    )

    pods = pods_r if not isinstance(pods_r, Exception) else []
    services = services_r if not isinstance(services_r, Exception) else []
    pvcs = pvcs_r if not isinstance(pvcs_r, Exception) else []
    ingresses = ingresses_r if not isinstance(ingresses_r, Exception) else []
    deployments = deployments_r if not isinstance(deployments_r, Exception) else []
    hpas = hpas_r if not isinstance(hpas_r, Exception) else []

    # Running pods keyed by (namespace, labels dict)
    running_pods = [
        p for p in pods
        if p.get("status", {}).get("phase") == "Running"
    ]

    # Collect PVC names claimed by pods
    claimed_pvcs: set[tuple[str, str]] = set()
    for pod in pods:
        pod_ns = pod.get("metadata", {}).get("namespace", "")
        for vol in pod.get("spec", {}).get("volumes", []):
            pvc_claim = vol.get("persistentVolumeClaim", {})
            if pvc_claim:
                claimed_pvcs.add((pod_ns, pvc_claim.get("claimName", "")))

    # HPA target names (deployments managed by HPA shouldn't be flagged for 0 replicas)
    hpa_targets: set[tuple[str, str]] = set()
    for hpa in hpas:
        hpa_ns = hpa.get("metadata", {}).get("namespace", "")
        ref = hpa.get("spec", {}).get("scaleTargetRef", {})
        hpa_targets.add((hpa_ns, ref.get("name", "")))

    # ── Orphaned services (selector matches no Running pod) ───────────────
    orphaned_services = []
    for svc in services:
        meta = svc.get("metadata", {})
        svc_ns = meta.get("name", "")
        selector = svc.get("spec", {}).get("selector") or {}
        if not selector:  # headless / external — skip
            continue
        svc_namespace = meta.get("namespace", "")
        # Check if any running pod in same namespace matches
        has_match = any(
            p.get("metadata", {}).get("namespace") == svc_namespace and
            _selector_matches_labels(selector, p.get("metadata", {}).get("labels", {}))
            for p in running_pods
        )
        if not has_match:
            orphaned_services.append({
                "name": meta.get("name", ""),
                "namespace": svc_namespace,
                "selector": selector,
                "type": svc.get("spec", {}).get("type", ""),
                "created_at": meta.get("creationTimestamp", ""),
                "reason": "No matching Running pods",
            })

    # ── Unbound PVCs ──────────────────────────────────────────────────────
    unbound_pvcs = []
    for pvc in pvcs:
        meta = pvc.get("metadata", {})
        pvc_ns = meta.get("namespace", "")
        pvc_name = meta.get("name", "")
        pvc_phase = pvc.get("status", {}).get("phase", "")
        spec = pvc.get("spec", {})
        # Flag if not bound OR bound but not claimed by any pod
        if pvc_phase != "Bound":
            unbound_pvcs.append({
                "name": pvc_name,
                "namespace": pvc_ns,
                "phase": pvc_phase,
                "storage": spec.get("resources", {}).get("requests", {}).get("storage", ""),
                "storage_class": spec.get("storageClassName", ""),
                "created_at": meta.get("creationTimestamp", ""),
                "reason": f"PVC phase is {pvc_phase}",
            })
        elif (pvc_ns, pvc_name) not in claimed_pvcs:
            unbound_pvcs.append({
                "name": pvc_name,
                "namespace": pvc_ns,
                "phase": pvc_phase,
                "storage": spec.get("resources", {}).get("requests", {}).get("storage", ""),
                "storage_class": spec.get("storageClassName", ""),
                "created_at": meta.get("creationTimestamp", ""),
                "reason": "Bound but not mounted by any pod",
            })

    # ── Orphaned ingresses (backend services don't exist) ─────────────────
    service_names: set[tuple[str, str]] = {
        (svc.get("metadata", {}).get("namespace", ""), svc.get("metadata", {}).get("name", ""))
        for svc in services
    }
    orphaned_ingresses = []
    for ing in ingresses:
        meta = ing.get("metadata", {})
        ing_ns = meta.get("namespace", "")
        missing_svcs = []
        for rule in ing.get("spec", {}).get("rules", []):
            http = rule.get("http", {})
            for path in http.get("paths", []):
                backend = path.get("backend", {})
                svc_name = (backend.get("service", {}).get("name") or
                            backend.get("serviceName", ""))
                if svc_name and (ing_ns, svc_name) not in service_names:
                    missing_svcs.append(svc_name)
        if missing_svcs:
            orphaned_ingresses.append({
                "name": meta.get("name", ""),
                "namespace": ing_ns,
                "missing_services": list(set(missing_svcs)),
                "created_at": meta.get("creationTimestamp", ""),
                "reason": f"References non-existent services: {', '.join(set(missing_svcs))}",
            })

    # ── Zero-replica deployments (not HPA-managed, no intentional annotation) ──
    zero_replica_deployments = []
    for dep in deployments:
        meta = dep.get("metadata", {})
        dep_ns = meta.get("namespace", "")
        dep_name = meta.get("name", "")
        spec_replicas = dep.get("spec", {}).get("replicas", 1)
        annotations = meta.get("annotations", {})
        # Skip if HPA-managed or explicitly annotated as intentionally scaled down
        if (dep_ns, dep_name) in hpa_targets:
            continue
        if "cluster-autoscaler.kubernetes.io/safe-to-evict" in annotations:
            continue
        if spec_replicas == 0:
            zero_replica_deployments.append({
                "name": dep_name,
                "namespace": dep_ns,
                "desired_replicas": 0,
                "created_at": meta.get("creationTimestamp", ""),
                "reason": "Scaled to 0 replicas",
            })

    return {
        "orphaned_services": orphaned_services,
        "unbound_pvcs": unbound_pvcs,
        "orphaned_ingresses": orphaned_ingresses,
        "zero_replica_deployments": zero_replica_deployments,
        "summary": {
            "orphaned_services": len(orphaned_services),
            "unbound_pvcs": len(unbound_pvcs),
            "orphaned_ingresses": len(orphaned_ingresses),
            "zero_replica_deployments": len(zero_replica_deployments),
            "total": len(orphaned_services) + len(unbound_pvcs) + len(orphaned_ingresses) + len(zero_replica_deployments),
        },
    }


# ── OOM Kill Detector (Loop 47) ──────────────────────────────────────────────

@app.get("/clusters/{cluster_id:path}/oom-detector")
async def oom_detector(
    cluster_id: str,
    namespace: str = "",
    restart_threshold: int = 5,
):
    """
    Scan all pods for OOMKilled containers or containers exceeding restart_threshold.
    Returns containers with their memory config, live usage (if available), and
    suggested memory limit.
    """
    client = await _cluster(cluster_id)
    pods_task, metrics_task = await asyncio.gather(
        kube_list(client, "pods", namespace or None),
        pod_metrics(cluster_id, namespace),
        return_exceptions=True,
    )
    pods = pods_task if not isinstance(pods_task, Exception) else []
    metrics_data = metrics_task if not isinstance(metrics_task, Exception) else {}

    # Build metrics lookup {ns/name: {container_name: mem_mib}}
    container_metrics: dict[str, dict[str, float]] = {}
    if isinstance(metrics_data, dict) and metrics_data.get("available"):
        for pm in metrics_data.get("pods", []):
            key = f"{pm['namespace']}/{pm['name']}"
            container_metrics[key] = {}
            for c in pm.get("containers", []):
                container_metrics[key][c["name"]] = c.get("mem_mib", 0)

    flagged = []
    for pod in pods:
        meta = pod.get("metadata", {})
        name = meta.get("name", "")
        ns = meta.get("namespace", "")
        pod_key = f"{ns}/{name}"
        phase = pod.get("status", {}).get("phase", "")

        spec_containers = {c["name"]: c for c in pod.get("spec", {}).get("containers", [])}
        status_containers = pod.get("status", {}).get("containerStatuses", [])
        init_statuses = pod.get("status", {}).get("initContainerStatuses", [])
        all_statuses = status_containers + init_statuses

        container_issues = []
        for cs in all_statuses:
            c_name = cs.get("name", "")
            restart_count = cs.get("restartCount", 0)

            last_state = cs.get("lastState", {})
            terminated = last_state.get("terminated", {})
            last_reason = terminated.get("reason", "")
            last_exit = terminated.get("exitCode")
            last_finished = terminated.get("finishedAt", "")

            current_state = cs.get("state", {})
            cur_terminated = current_state.get("terminated", {})
            cur_reason = cur_terminated.get("reason", "") if cur_terminated else ""

            is_oom = last_reason == "OOMKilled" or cur_reason == "OOMKilled"
            high_restarts = restart_count >= restart_threshold

            if not (is_oom or high_restarts):
                continue

            spec_c = spec_containers.get(c_name, {})
            res = spec_c.get("resources", {})
            req_mem = round(_mem_to_mib(res.get("requests", {}).get("memory", "0")))
            lim_mem = round(_mem_to_mib(res.get("limits", {}).get("memory", "0")))

            live_mem = None
            pm_map = container_metrics.get(pod_key, {})
            if c_name in pm_map:
                live_mem = round(pm_map[c_name])

            # Suggest new limit: max(lim_mem * 1.5, live_mem * 1.5) rounded up
            suggested_mib = None
            if lim_mem > 0:
                base = max(lim_mem, live_mem or 0)
                suggested_mib = round(base * 1.5 / 64) * 64  # round to nearest 64 MiB

            container_issues.append({
                "name": c_name,
                "restart_count": restart_count,
                "is_oom": is_oom,
                "last_reason": last_reason or cur_reason,
                "last_exit_code": last_exit,
                "last_finished": last_finished,
                "req_mem_mib": req_mem,
                "lim_mem_mib": lim_mem,
                "live_mem_mib": live_mem,
                "suggested_limit_mib": suggested_mib,
            })

        if container_issues:
            flagged.append({
                "name": name,
                "namespace": ns,
                "phase": phase,
                "containers": container_issues,
                "total_restarts": sum(c["restart_count"] for c in container_issues),
                "has_oom": any(c["is_oom"] for c in container_issues),
            })

    flagged.sort(key=lambda p: (-int(p["has_oom"]), -p["total_restarts"]))
    oom_count = sum(1 for p in flagged if p["has_oom"])
    return {
        "pods": flagged,
        "total_flagged": len(flagged),
        "oom_pods": oom_count,
        "metrics_available": isinstance(metrics_data, dict) and metrics_data.get("available", False),
    }


# ── TLS Certificate Expiry Scanner (Loop 46) ─────────────────────────────────

import base64 as _b64
from datetime import datetime as _dt, timezone as _tz


def _parse_tls_cert(pem_bytes: bytes) -> dict | None:
    """Extract CN, SANs, and expiry from a PEM certificate using cryptography."""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        cert = x509.load_pem_x509_certificate(pem_bytes, default_backend())
        cn = ""
        try:
            cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
        except Exception:
            pass
        sans: list[str] = []
        try:
            san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            for name in san_ext.value:
                if isinstance(name, x509.DNSName):
                    sans.append(name.value)
                elif isinstance(name, x509.IPAddress):
                    sans.append(str(name.value))
        except Exception:
            pass
        expiry = cert.not_valid_after_utc
        now = _dt.now(_tz.utc)
        days_remaining = (expiry - now).days
        return {
            "cn": cn,
            "sans": sans,
            "expiry": expiry.isoformat(),
            "days_remaining": days_remaining,
        }
    except Exception:
        return None


@app.get("/clusters/{cluster_id:path}/tls-certs")
async def list_tls_certs(
    cluster_id: str,
    namespace: str = "",
    days_warning: int = 30,
):
    """Scan TLS-type secrets, decode x509 certs, return expiry info sorted by days_remaining."""
    client = await _cluster(cluster_id)
    # Fetch directly to bypass redaction — cert data is needed for x509 parsing
    ns_seg = f"/namespaces/{namespace}" if namespace else ""
    resp = await client.get(
        f"/api/v1{ns_seg}/secrets",
        params={"limit": 500, "fieldSelector": "type=kubernetes.io/tls"},
    )
    secrets = resp.json().get("items", []) if resp.status_code == 200 else []

    certs = []
    for secret in secrets:
        meta = secret.get("metadata", {})
        if secret.get("type") != "kubernetes.io/tls":
            continue
        data = secret.get("data", {})
        pem_b64 = data.get("tls.crt", "")
        if not pem_b64:
            continue
        try:
            pem_bytes = _b64.b64decode(pem_b64)
        except Exception:
            continue
        parsed = _parse_tls_cert(pem_bytes)
        if not parsed:
            continue

        days = parsed["days_remaining"]
        if days < 0:
            status = "expired"
        elif days < days_warning:
            status = "warning"
        else:
            status = "ok"

        certs.append({
            "secret_name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "cn": parsed["cn"],
            "sans": parsed["sans"],
            "expiry": parsed["expiry"],
            "days_remaining": days,
            "status": status,
        })

    # Sort: expired first, then by days_remaining ascending, then ok
    certs.sort(key=lambda c: (0 if c["status"] == "expired" else 1 if c["status"] == "warning" else 2, c["days_remaining"]))
    expired_count = sum(1 for c in certs if c["status"] == "expired")
    warning_count = sum(1 for c in certs if c["status"] == "warning")
    return {
        "certs": certs,
        "total": len(certs),
        "expired": expired_count,
        "warning": warning_count,
        "ok": len(certs) - expired_count - warning_count,
    }


# ── Network Policy Traffic Analyzer (Loop 45) ─────────────────────────────────

def _parse_label_selector(labels_str: str) -> dict[str, str]:
    """Parse 'k=v,k2=v2' into {k: v, k2: v2}."""
    if not labels_str:
        return {}
    result = {}
    for pair in labels_str.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _labels_match_selector(pod_labels: dict, selector: dict) -> bool:
    """Return True if pod_labels satisfy all key=value in selector."""
    for k, v in selector.items():
        if pod_labels.get(k) != v:
            return False
    return True


def _pod_selector_matches(pod_labels: dict, pod_selector: dict) -> bool:
    """Evaluate a NetworkPolicy podSelector against pod labels."""
    match_labels = pod_selector.get("matchLabels", {})
    match_exprs = pod_selector.get("matchExpressions", [])
    if not _labels_match_selector(pod_labels, match_labels):
        return False
    for expr in match_exprs:
        key = expr.get("key", "")
        op = expr.get("operator", "")
        values = expr.get("values", [])
        pod_val = pod_labels.get(key)
        if op == "In" and pod_val not in values:
            return False
        if op == "NotIn" and pod_val in values:
            return False
        if op == "Exists" and key not in pod_labels:
            return False
        if op == "DoesNotExist" and key in pod_labels:
            return False
    return True


def _port_matches(rule_ports: list, query_port: int, query_proto: str) -> bool:
    """Return True if query port/protocol is allowed by rule_ports (empty = all allowed)."""
    if not rule_ports:
        return True
    for rp in rule_ports:
        rp_proto = rp.get("protocol", "TCP").upper()
        rp_port = rp.get("port")
        if rp_proto != query_proto.upper():
            continue
        if rp_port is None or rp_port == query_port or str(rp_port) == str(query_port):
            return True
    return False


def _peer_matches_source(peer: dict, src_labels: dict, src_ns: str, ns_labels_map: dict) -> bool:
    """Check if a NetworkPolicy peer description allows traffic from src pod."""
    # podSelector must match src labels
    pod_sel = peer.get("podSelector")
    ns_sel = peer.get("namespaceSelector")
    ip_block = peer.get("ipBlock")

    if ip_block:
        return False  # IP-based rules not evaluated here

    if pod_sel is not None and ns_sel is None:
        # Same namespace implied
        return _pod_selector_matches(src_labels, pod_sel)

    if ns_sel is not None and pod_sel is None:
        # Any pod in matching namespaces
        src_ns_labels = ns_labels_map.get(src_ns, {})
        return _pod_selector_matches(src_ns_labels, ns_sel)

    if pod_sel is not None and ns_sel is not None:
        src_ns_labels = ns_labels_map.get(src_ns, {})
        return (_pod_selector_matches(src_labels, pod_sel) and
                _pod_selector_matches(src_ns_labels, ns_sel))

    # Empty peer = allow all
    return True


def _analyze_ingress(
    policies: list, dst_labels: dict, dst_ns: str,
    src_labels: dict, src_ns: str, src_ns_labels: dict,
    port: int, protocol: str,
) -> tuple[bool, list[str], list[str]]:
    """
    Evaluate ingress NetworkPolicies for dst pod.
    Returns (allowed, matching_policy_names, blocking_policy_names).
    """
    ns_labels_map = {src_ns: src_ns_labels}

    # Filter policies that select the dst pod and have Ingress type
    ingress_policies = [
        p for p in policies
        if "Ingress" in p.get("spec", {}).get("policyTypes", []) or
        p.get("spec", {}).get("ingress") is not None
    ]
    selecting_policies = [
        p for p in ingress_policies
        if _pod_selector_matches(dst_labels, p.get("spec", {}).get("podSelector", {}))
    ]

    if not selecting_policies:
        # No policies select this pod → ingress is open (implicit allow)
        return True, [], []

    # At least one policy selects this pod — now check if any allows the traffic
    allowed_by: list[str] = []
    blocked_by: list[str] = []

    for p in selecting_policies:
        pol_name = p.get("metadata", {}).get("name", "?")
        ingress_rules = p.get("spec", {}).get("ingress") or []

        if ingress_rules == [] or ingress_rules is None:
            # Policy selects pod but has no ingress rules → denies all ingress
            blocked_by.append(pol_name)
            continue

        rule_allows = False
        for rule in ingress_rules:
            froms = rule.get("from", [])
            ports = rule.get("ports", [])

            if not _port_matches(ports, port, protocol):
                continue

            if not froms:
                # Empty from → allow from anywhere
                rule_allows = True
                break

            for peer in froms:
                if _peer_matches_source(peer, src_labels, src_ns, ns_labels_map):
                    rule_allows = True
                    break
            if rule_allows:
                break

        if rule_allows:
            allowed_by.append(pol_name)
        else:
            blocked_by.append(pol_name)

    # Traffic is allowed only if at least one policy allows AND none explicitly deny
    # K8s NetworkPolicy semantics: if any selecting policy's rules match → allow
    overall = len(allowed_by) > 0
    return overall, allowed_by, blocked_by


def _analyze_egress(
    policies: list, src_labels: dict, src_ns: str,
    dst_labels: dict, dst_ns: str, dst_ns_labels: dict,
    port: int, protocol: str,
) -> tuple[bool, list[str], list[str]]:
    """
    Evaluate egress NetworkPolicies for src pod.
    Returns (allowed, matching_policy_names, blocking_policy_names).
    """
    ns_labels_map = {dst_ns: dst_ns_labels}

    egress_policies = [
        p for p in policies
        if "Egress" in p.get("spec", {}).get("policyTypes", []) or
        p.get("spec", {}).get("egress") is not None
    ]
    selecting_policies = [
        p for p in egress_policies
        if _pod_selector_matches(src_labels, p.get("spec", {}).get("podSelector", {}))
    ]

    if not selecting_policies:
        return True, [], []

    allowed_by: list[str] = []
    blocked_by: list[str] = []

    for p in selecting_policies:
        pol_name = p.get("metadata", {}).get("name", "?")
        egress_rules = p.get("spec", {}).get("egress") or []

        if egress_rules == [] or egress_rules is None:
            blocked_by.append(pol_name)
            continue

        rule_allows = False
        for rule in egress_rules:
            tos = rule.get("to", [])
            ports = rule.get("ports", [])

            if not _port_matches(ports, port, protocol):
                continue

            if not tos:
                rule_allows = True
                break

            for peer in tos:
                pod_sel = peer.get("podSelector")
                ns_sel = peer.get("namespaceSelector")
                dst_ns_labels_actual = ns_labels_map.get(dst_ns, {})

                if pod_sel is None and ns_sel is None:
                    rule_allows = True
                    break
                if pod_sel is not None and ns_sel is None:
                    if _pod_selector_matches(dst_labels, pod_sel):
                        rule_allows = True
                        break
                if ns_sel is not None and pod_sel is None:
                    if _pod_selector_matches(dst_ns_labels_actual, ns_sel):
                        rule_allows = True
                        break
                if pod_sel is not None and ns_sel is not None:
                    if (_pod_selector_matches(dst_labels, pod_sel) and
                            _pod_selector_matches(dst_ns_labels_actual, ns_sel)):
                        rule_allows = True
                        break
            if rule_allows:
                break

        if rule_allows:
            allowed_by.append(pol_name)
        else:
            blocked_by.append(pol_name)

    overall = len(allowed_by) > 0
    return overall, allowed_by, blocked_by


@app.get("/clusters/{cluster_id:path}/netpol/analyze")
async def netpol_analyze(
    cluster_id: str,
    src_ns: str = "default",
    src_labels: str = "",
    dst_ns: str = "default",
    dst_labels: str = "",
    port: int = 80,
    protocol: str = "TCP",
):
    """
    Evaluate NetworkPolicy rules to determine if traffic from src pod to dst pod
    on the given port/protocol would be allowed.
    """
    client = await _cluster(cluster_id)

    src_label_map = _parse_label_selector(src_labels)
    dst_label_map = _parse_label_selector(dst_labels)

    # Fetch all required data in parallel
    fetch_tasks = [
        kube_list(client, "networkpolicies", src_ns),
        kube_list(client, "namespaces"),
    ]
    if dst_ns != src_ns:
        fetch_tasks.append(kube_list(client, "networkpolicies", dst_ns))

    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    src_policies = results[0] if not isinstance(results[0], Exception) else []
    ns_list = results[1] if not isinstance(results[1], Exception) else []
    dst_policies = results[2] if (len(results) > 2 and not isinstance(results[2], Exception)) else (
        src_policies if dst_ns == src_ns else []
    )

    # Build namespace labels map
    ns_labels_map: dict[str, dict] = {}
    for ns_obj in ns_list:
        ns_name = ns_obj.get("metadata", {}).get("name", "")
        ns_labels_map[ns_name] = ns_obj.get("metadata", {}).get("labels", {})

    src_ns_labels = ns_labels_map.get(src_ns, {})
    dst_ns_labels = ns_labels_map.get(dst_ns, {})

    # Analyze ingress on dst
    ingress_allowed, ingress_allowed_by, ingress_blocked_by = _analyze_ingress(
        dst_policies, dst_label_map, dst_ns,
        src_label_map, src_ns, src_ns_labels,
        port, protocol,
    )

    # Analyze egress on src
    egress_allowed, egress_allowed_by, egress_blocked_by = _analyze_egress(
        src_policies, src_label_map, src_ns,
        dst_label_map, dst_ns, dst_ns_labels,
        port, protocol,
    )

    if ingress_allowed and egress_allowed:
        verdict = "allowed"
    elif not ingress_allowed and not egress_allowed:
        verdict = "blocked_by_both"
    elif not ingress_allowed:
        verdict = "blocked_by_ingress"
    else:
        verdict = "blocked_by_egress"

    return {
        "verdict": verdict,
        "allowed": verdict == "allowed",
        "src": {"namespace": src_ns, "labels": src_label_map},
        "dst": {"namespace": dst_ns, "labels": dst_label_map, "port": port, "protocol": protocol},
        "ingress": {
            "allowed": ingress_allowed,
            "allowed_by": ingress_allowed_by,
            "blocked_by": ingress_blocked_by,
            "policy_count": len([p for p in dst_policies
                                  if "Ingress" in p.get("spec", {}).get("policyTypes", [])
                                  or p.get("spec", {}).get("ingress") is not None]),
        },
        "egress": {
            "allowed": egress_allowed,
            "allowed_by": egress_allowed_by,
            "blocked_by": egress_blocked_by,
            "policy_count": len([p for p in src_policies
                                  if "Egress" in p.get("spec", {}).get("policyTypes", [])
                                  or p.get("spec", {}).get("egress") is not None]),
        },
    }


@app.post("/clusters/{cluster_id:path}/pods/batch-restart")
async def batch_restart_pods(cluster_id: str, request: Request, token: str = Query("")):
    """Delete multiple pods at once (triggers restart for pods owned by a controller).
    
    Body: {"pods": [{"name": str, "namespace": str}, ...]}
    """
    body = await request.json()
    pods_list: list = body.get("pods", [])
    if not pods_list:
        raise HTTPException(400, "pods list required")
    if len(pods_list) > 50:
        raise HTTPException(400, "Cannot restart more than 50 pods at once")

    description = f"Batch restart {len(pods_list)} pods: " + ", ".join(
        f"{p.get('namespace', '')}/{p.get('name', '')}" for p in pods_list[:5]
    ) + ("..." if len(pods_list) > 5 else "")

    if not token:
        return issue_token("batch-restart-pods", "multiple",
                           {"description": description, "pods": pods_list})
    _consume(token)
    user = _user(request)
    client = await _cluster(cluster_id)

    results = []
    for pod_ref in pods_list:
        name = pod_ref.get("name", "")
        ns = pod_ref.get("namespace", "default")
        if not name:
            continue
        resp = await client.delete(f"/api/v1/namespaces/{ns}/pods/{name}")
        results.append({
            "name": name, "namespace": ns,
            "ok": resp.status_code in (200, 202),
            "status": resp.status_code,
        })

    await audit_emit(user, "batch-restart-pods", cluster_id, "", "Pod", "multiple",
                     {"count": len(results), "pods": [r["name"] for r in results]})
    return {"ok": True, "results": results}



@app.get("/clusters/{cluster_id:path}/nodes/metrics")
async def node_metrics(cluster_id: str):
    """Return live CPU/memory per node from metrics-server."""
    client = await _cluster(cluster_id)
    try:
        resp = await client.get("/apis/metrics.k8s.io/v1beta1/nodes")
        if resp.status_code in (404, 503, 501):
            return {"available": False, "nodes": []}
        resp.raise_for_status()
        items = resp.json().get("items", [])
        nodes = []
        for item in items:
            meta = item.get("metadata", {})
            usage = item.get("usage", {})
            nodes.append({
                "name": meta.get("name", ""),
                "cpu_m": round(_cpu_to_m(usage.get("cpu", "0"))),
                "mem_mib": round(_mem_to_mib(usage.get("memory", "0"))),
            })
        return {"available": True, "nodes": nodes}
    except Exception:
        return {"available": False, "nodes": []}


@app.get("/clusters/{cluster_id:path}/services")
async def list_services(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "services", namespace or None)
    return {"services": [_format_service(s) for s in items]}


@app.get("/clusters/{cluster_id:path}/services/{name}/endpoints")
async def service_endpoints(cluster_id: str, name: str, namespace: str = Query(...)):
    client = await _cluster(cluster_id)
    try:
        resp = await client.get(f"/api/v1/namespaces/{namespace}/endpoints/{name}")
        if resp.status_code == 404:
            return {"ready": [], "not_ready": []}
        resp.raise_for_status()
        ep = resp.json()
        ready: list[dict] = []
        not_ready: list[dict] = []
        for subset in ep.get("subsets", []):
            ports = [f"{p.get('port', '*')}/{p.get('protocol', 'TCP')}"
                     for p in subset.get("ports", [])]
            for addr in subset.get("addresses", []):
                ready.append({
                    "ip": addr.get("ip", ""),
                    "node": addr.get("nodeName", ""),
                    "target": (addr.get("targetRef") or {}).get("name", ""),
                    "ports": ports,
                })
            for addr in subset.get("notReadyAddresses", []):
                not_ready.append({
                    "ip": addr.get("ip", ""),
                    "node": addr.get("nodeName", ""),
                    "target": (addr.get("targetRef") or {}).get("name", ""),
                    "ports": ports,
                })
        return {"ready": ready, "not_ready": not_ready}
    except Exception as e:
        return {"ready": [], "not_ready": [], "error": str(e)}


@app.get("/clusters/{cluster_id:path}/ingresses")
async def list_ingresses(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "ingresses", namespace or None)
    return {"ingresses": [_format_ingress(i) for i in items]}


@app.patch("/clusters/{cluster_id:path}/services/{name}")
async def patch_service(cluster_id: str, name: str, request: Request,
                        namespace: str = Query(...), token: str = Query("")):
    body = await request.json()
    svc_type = body.get("type")
    ports = body.get("ports")
    if not svc_type and not ports:
        raise HTTPException(400, "type or ports required")
    if token:
        try:
            consume_token(token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        client = await _cluster(cluster_id)
        patch: dict = {"spec": {}}
        if svc_type:
            patch["spec"]["type"] = svc_type
        if ports:
            patch["spec"]["ports"] = ports
        await kube_patch(client, "services", name, namespace, patch)
        return {"ok": True}
    return issue_token("patch-service", f"{namespace}/{name}", {"type": svc_type, "ports": ports})


@app.delete("/clusters/{cluster_id:path}/services/{name}")
async def delete_service(cluster_id: str, name: str,
                         namespace: str = Query(...), token: str = Query("")):
    if token:
        try:
            consume_token(token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        client = await _cluster(cluster_id)
        await kube_delete(client, "services", name, namespace)
        return {"ok": True}
    return issue_token("delete-service", f"{namespace}/{name}", {})


@app.post("/clusters/{cluster_id:path}/ingresses")
async def create_ingress(cluster_id: str, request: Request,
                         namespace: str = Query(...), token: str = Query("")):
    body = await request.json()
    ing_name = body.get("name", "")
    host = body.get("host", "")
    service_name = body.get("service_name", "")
    service_port = body.get("service_port")
    path = body.get("path", "/")
    path_type = body.get("path_type", "Prefix")
    ingress_class = body.get("ingress_class", "")
    tls = body.get("tls", False)
    if not ing_name or not service_name or not service_port:
        raise HTTPException(400, "name, service_name, service_port required")
    if token:
        try:
            consume_token(token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        manifest: dict = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {"name": ing_name, "namespace": namespace},
            "spec": {
                "rules": [{"host": host, "http": {"paths": [{
                    "path": path,
                    "pathType": path_type,
                    "backend": {"service": {"name": service_name, "port": {"number": int(service_port)}}},
                }]}}],
            },
        }
        if ingress_class:
            manifest["metadata"]["annotations"] = {"kubernetes.io/ingress.class": ingress_class}
        if tls and host:
            manifest["spec"]["tls"] = [{"hosts": [host], "secretName": f"{ing_name}-tls"}]
        client = await _cluster(cluster_id)
        await kube_apply(client, manifest)
        return {"ok": True, "name": ing_name}
    return issue_token("create-ingress", f"{namespace}/{ing_name}", {
        "host": host, "service": service_name, "port": service_port
    })


@app.delete("/clusters/{cluster_id:path}/ingresses/{name}")
async def delete_ingress(cluster_id: str, name: str,
                         namespace: str = Query(...), token: str = Query("")):
    if token:
        try:
            consume_token(token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        client = await _cluster(cluster_id)
        await kube_delete(client, "ingresses", name, namespace)
        return {"ok": True}
    return issue_token("delete-ingress", f"{namespace}/{name}", {})


@app.get("/clusters/{cluster_id:path}/networkpolicies")
async def list_networkpolicies(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "networkpolicies", namespace or None)
    return {"networkpolicies": [_format_networkpolicy(i) for i in items]}


@app.get("/clusters/{cluster_id:path}/quotas")
async def list_quotas(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "resourcequotas", namespace or None)
    return {"quotas": [_format_quota(q) for q in items]}


@app.get("/clusters/{cluster_id:path}/health-report")
async def cluster_health_report(cluster_id: str):
    """Return a comprehensive cluster health report suitable for export."""
    import datetime
    client = await _cluster(cluster_id)

    nodes_r, pods_r, deps_r, sts_r, dsets_r, events_r, quotas_r = await asyncio.gather(
        kube_list(client, "nodes"),
        kube_list(client, "pods"),
        kube_list(client, "deployments"),
        kube_list(client, "statefulsets"),
        kube_list(client, "daemonsets"),
        kube_list(client, "events"),
        kube_list(client, "resourcequotas"),
        return_exceptions=True,
    )

    def _safe(v, default):
        return v if not isinstance(v, Exception) else default

    nodes = _safe(nodes_r, [])
    pods = _safe(pods_r, [])
    deps = _safe(deps_r, [])
    sts = _safe(sts_r, [])
    dsets = _safe(dsets_r, [])
    events = _safe(events_r, [])
    quotas = _safe(quotas_r, [])

    # Node summary
    node_rows = []
    for n in nodes:
        meta = n.get("metadata", {})
        status = n.get("status", {})
        conds = status.get("conditions", [])
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds)
        alloc = status.get("allocatable", {})
        taints = n.get("spec", {}).get("taints") or []
        roles = [k.split("/", 1)[1] for k in meta.get("labels", {}) if k.startswith("node-role.kubernetes.io/")]
        node_rows.append({
            "name": meta.get("name", ""),
            "ready": ready,
            "roles": roles,
            "taints": len(taints),
            "cpu_allocatable_m": _cpu_to_m(alloc.get("cpu", "0")),
            "mem_allocatable_mib": _mem_to_mib(alloc.get("memory", "0")),
        })

    # Pod summary
    phase_counts: dict = {}
    crashloop_pods = []
    for p in pods:
        phase = p.get("status", {}).get("phase", "Unknown")
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
        for cs in p.get("status", {}).get("containerStatuses", []):
            if cs.get("state", {}).get("waiting", {}).get("reason") == "CrashLoopBackOff":
                meta = p.get("metadata", {})
                crashloop_pods.append({"name": meta.get("name", ""), "namespace": meta.get("namespace", "")})
                break

    # Workload health
    degraded = []
    for w in [*deps, *sts]:
        meta = w.get("metadata", {})
        spec = w.get("spec", {})
        wstatus = w.get("status", {})
        desired = spec.get("replicas", 0) or 0
        ready = wstatus.get("readyReplicas", 0) or 0
        kind = "Deployment" if w in deps else "StatefulSet"
        if desired > 0 and ready < desired:
            degraded.append({"name": meta.get("name", ""), "namespace": meta.get("namespace", ""), "kind": kind, "ready": ready, "desired": desired})

    # Quota pressure (> 70% usage)
    quota_pressure = []
    for q in quotas:
        meta = q.get("metadata", {})
        hard = q.get("spec", {}).get("hard", {})
        used_map = q.get("status", {}).get("used", {})
        for resource, hard_val in hard.items():
            used_val = used_map.get(resource, "0")
            h = _parse_quantity(hard_val) if hard_val else 0
            u = _parse_quantity(used_val) if used_val else 0
            pct = round(u / h * 100) if h > 0 else 0
            if pct >= 70:
                quota_pressure.append({
                    "namespace": meta.get("namespace", ""),
                    "quota": meta.get("name", ""),
                    "resource": resource,
                    "used": used_val,
                    "hard": hard_val,
                    "pct": pct,
                })

    # Warning events
    warning_events = [
        _format_event(e) for e in events
        if e.get("type") == "Warning"
    ]
    warning_events.sort(key=lambda e: e.get("last_time") or "", reverse=True)

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cluster_id": cluster_id,
        "nodes": node_rows,
        "pods": {**phase_counts, "total": len(pods)},
        "workloads": {
            "deployments": len(deps),
            "statefulsets": len(sts),
            "daemonsets": len(dsets),
            "degraded": degraded,
        },
        "crashloop_pods": crashloop_pods,
        "quota_pressure": sorted(quota_pressure, key=lambda x: x["pct"], reverse=True),
        "warning_events": warning_events[:50],
        "summary": {
            "nodes_ready": sum(1 for n in node_rows if n["ready"]),
            "nodes_total": len(node_rows),
            "pods_running": phase_counts.get("Running", 0),
            "pods_total": len(pods),
            "crashloop_count": len(crashloop_pods),
            "degraded_workloads": len(degraded),
            "quota_pressure_count": len(quota_pressure),
            "warning_event_count": len(warning_events),
        },
    }


def _parse_quantity(val: str) -> float:
    """Parse a Kubernetes quantity string to a float for comparison."""
    if not val:
        return 0.0
    if val.endswith("m"):
        return float(val[:-1]) / 1000
    if val.endswith("Ki"):
        return float(val[:-2]) * 1024
    if val.endswith("Mi"):
        return float(val[:-2]) * 1024 * 1024
    if val.endswith("Gi"):
        return float(val[:-2]) * 1024 * 1024 * 1024
    try:
        return float(val)
    except ValueError:
        return 0.0


@app.get("/clusters/{cluster_id:path}/topology")
async def cluster_topology(cluster_id: str, namespace: str = ""):
    """Return workload→service→ingress dependency graph for topology view."""
    client = await _cluster(cluster_id)
    deps_r, sts_r, svcs_r, ings_r = await asyncio.gather(
        kube_list(client, "deployments", namespace or None),
        kube_list(client, "statefulsets", namespace or None),
        kube_list(client, "services", namespace or None),
        kube_list(client, "ingresses", namespace or None),
        return_exceptions=True,
    )

    def _safe(v):
        return v if not isinstance(v, Exception) else []

    deps = _safe(deps_r)
    sts = _safe(sts_r)
    svcs = _safe(svcs_r)
    ings = _safe(ings_r)

    def _selector_matches(svc_sel: dict, workload_labels: dict) -> bool:
        if not svc_sel:
            return False
        return all(workload_labels.get(k) == v for k, v in svc_sel.items())

    workloads = []
    for w in [*deps, *sts]:
        meta = w.get("metadata", {})
        spec = w.get("spec", {})
        status = w.get("status", {})
        kind = "Deployment" if w in deps else "StatefulSet"
        sel = spec.get("selector", {}).get("matchLabels", {})
        workloads.append({
            "id": f"{kind.lower()}/{meta.get('namespace', '')}/{meta.get('name', '')}",
            "kind": kind,
            "type": kind.lower(),
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "ready": (status.get("readyReplicas") or 0) >= (spec.get("replicas") or 1),
            "selector": sel,
        })

    services = []
    for svc in svcs:
        meta = svc.get("metadata", {})
        spec = svc.get("spec", {})
        svc_ns = meta.get("namespace", "")
        svc_name = meta.get("name", "")
        svc_sel = spec.get("selector") or {}
        svc_type = spec.get("type", "ClusterIP")
        ports = [f"{p.get('port', '')}/{p.get('protocol', 'TCP')}" for p in spec.get("ports", [])]
        targets = [w["id"] for w in workloads if w["namespace"] == svc_ns and _selector_matches(svc_sel, w["selector"])]
        services.append({
            "id": f"service/{svc_ns}/{svc_name}",
            "name": svc_name,
            "namespace": svc_ns,
            "type": svc_type,
            "ports": ports,
            "targets": targets,
            "has_selector": bool(svc_sel),
        })

    svc_map = {f"{s['namespace']}/{s['name']}": s["id"] for s in services}

    ingresses = []
    for ing in ings:
        meta = ing.get("metadata", {})
        spec = ing.get("spec", {})
        ing_ns = meta.get("namespace", "")
        ing_name = meta.get("name", "")
        rules_out = []
        for rule in spec.get("rules", []):
            host = rule.get("host", "*")
            http = rule.get("http", {})
            paths_out = []
            for path in http.get("paths", []):
                backend = path.get("backend", {})
                svc_ref = backend.get("service", {})
                svc_name = svc_ref.get("name", "") or backend.get("serviceName", "")
                svc_port = svc_ref.get("port", {}).get("number") or backend.get("servicePort", "")
                svc_id = svc_map.get(f"{ing_ns}/{svc_name}", "")
                paths_out.append({
                    "path": path.get("path", "/"),
                    "path_type": path.get("pathType", ""),
                    "service": svc_name,
                    "service_id": svc_id,
                    "port": svc_port,
                })
            rules_out.append({"host": host, "paths": paths_out})
        ingresses.append({
            "id": f"ingress/{ing_ns}/{ing_name}",
            "name": ing_name,
            "namespace": ing_ns,
            "rules": rules_out,
        })

    return {
        "workloads": workloads,
        "services": services,
        "ingresses": ingresses,
    }


@app.get("/clusters/{cluster_id:path}/images")
async def list_images(cluster_id: str, namespace: str = ""):
    """Aggregate container images across pods, grouped by image:tag."""
    client = await _cluster(cluster_id)
    pods = await kube_list(client, "pods", namespace or None)

    images: dict[str, dict] = {}
    for pod in pods:
        meta = pod.get("metadata", {})
        pod_ns = meta.get("namespace", "")
        pod_name = meta.get("name", "")
        pod_phase = pod.get("status", {}).get("phase", "Unknown")
        spec = pod.get("spec", {})
        all_containers = spec.get("containers", []) + spec.get("initContainers", [])
        for c in all_containers:
            image = c.get("image", "")
            if not image:
                continue
            if image not in images:
                last_part = image.split("/")[-1]
                if ":" in last_part:
                    tag = last_part.rsplit(":", 1)[1]
                    short = last_part.rsplit(":", 1)[0]
                else:
                    tag = "latest"
                    short = last_part
                is_latest = tag == "latest"
                is_sha = "@sha256:" in image
                images[image] = {
                    "image": image,
                    "short": short,
                    "tag": tag,
                    "is_latest": is_latest,
                    "is_pinned": is_sha,
                    "pods": [],
                    "namespaces": set(),
                }
            images[image]["pods"].append({
                "name": pod_name,
                "namespace": pod_ns,
                "phase": pod_phase,
            })
            images[image]["namespaces"].add(pod_ns)

    result = []
    for img_data in images.values():
        result.append({
            "image": img_data["image"],
            "short": img_data["short"],
            "tag": img_data["tag"],
            "is_latest": img_data["is_latest"],
            "is_pinned": img_data["is_pinned"],
            "pod_count": len(img_data["pods"]),
            "pods": sorted(img_data["pods"], key=lambda p: p["name"])[:20],
            "namespaces": sorted(img_data["namespaces"]),
        })
    result.sort(key=lambda x: x["pod_count"], reverse=True)
    return {"images": result, "total": len(result)}


@app.get("/clusters/{cluster_id:path}/pdbs")
async def list_pdbs(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "poddisruptionbudgets", namespace or None)
    return {"pdbs": [_format_pdb(p) for p in items]}


@app.post("/clusters/{cluster_id:path}/pdbs")
async def create_pdb(cluster_id: str, body: dict = Body(...),
                     namespace: str = Query("default"), token: str = Query("")):
    """Create a PodDisruptionBudget. Provide name, selector labels, and either
    min_available or max_unavailable (integer or percentage string)."""
    pdb_name = body.get("name")
    selector = body.get("selector")  # dict of label k/v
    min_available = body.get("min_available")
    max_unavailable = body.get("max_unavailable")
    if not pdb_name or not selector:
        raise HTTPException(400, "name and selector are required")
    if min_available is None and max_unavailable is None:
        raise HTTPException(400, "Provide min_available or max_unavailable")
    if not token:
        return issue_token("create-pdb", f"{namespace}/{pdb_name}", {"selector": selector})
    consume_token(token)
    client = await _cluster(cluster_id)
    spec: dict = {"selector": {"matchLabels": selector}}
    if min_available is not None:
        spec["minAvailable"] = min_available
    else:
        spec["maxUnavailable"] = max_unavailable
    manifest = {
        "apiVersion": "policy/v1",
        "kind": "PodDisruptionBudget",
        "metadata": {"name": pdb_name, "namespace": namespace},
        "spec": spec,
    }
    await kube_apply(client, manifest)
    return {"ok": True, "name": pdb_name}


@app.delete("/clusters/{cluster_id:path}/pdbs/{name}")
async def delete_pdb(cluster_id: str, name: str,
                     namespace: str = Query("default"), token: str = Query("")):
    if not token:
        return issue_token("delete-pdb", f"{namespace}/{name}", {})
    consume_token(token)
    client = await _cluster(cluster_id)
    await kube_delete(client, "poddisruptionbudgets", name, namespace)
    return {"ok": True}


@app.get("/clusters/{cluster_id:path}/limitranges")
async def list_limitranges(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "limitranges", namespace or None)
    return {"limitranges": [_format_limitrange(lr) for lr in items]}


@app.get("/clusters/{cluster_id:path}/serviceaccounts")
async def list_serviceaccounts(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "serviceaccounts", namespace or None)
    return {"serviceaccounts": [_format_serviceaccount(sa) for sa in items]}


@app.post("/clusters/{cluster_id:path}/serviceaccounts")
async def create_serviceaccount(cluster_id: str, body: dict = Body(...),
                                namespace: str = Query("default"), token: str = Query("")):
    """Create a ServiceAccount, optionally attaching image pull secrets."""
    sa_name = body.get("name")
    if not sa_name:
        raise HTTPException(400, "name is required")
    pull_secrets: list[str] = body.get("image_pull_secrets", [])
    labels: dict = body.get("labels", {})
    if not token:
        return issue_token("create-sa", f"{namespace}/{sa_name}", {"pull_secrets": pull_secrets})
    consume_token(token)
    client = await _cluster(cluster_id)
    manifest: dict = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": sa_name, "namespace": namespace, **({"labels": labels} if labels else {})},
    }
    if pull_secrets:
        manifest["imagePullSecrets"] = [{"name": s} for s in pull_secrets]
    await kube_apply(client, manifest)
    return {"ok": True, "name": sa_name}


@app.delete("/clusters/{cluster_id:path}/serviceaccounts/{name}")
async def delete_serviceaccount(cluster_id: str, name: str,
                                namespace: str = Query("default"), token: str = Query("")):
    if name in ("default",):
        raise HTTPException(400, "Cannot delete the 'default' ServiceAccount")
    if not token:
        return issue_token("delete-sa", f"{namespace}/{name}", {})
    consume_token(token)
    client = await _cluster(cluster_id)
    await kube_delete(client, "serviceaccounts", name, namespace)
    return {"ok": True}


@app.get("/clusters/{cluster_id:path}/rbac")
async def list_rbac(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    rbs_r, crbs_r = await asyncio.gather(
        kube_list(client, "rolebindings", namespace or None),
        kube_list(client, "clusterrolebindings"),
        return_exceptions=True,
    )
    rolebindings = rbs_r if not isinstance(rbs_r, Exception) else []
    clusterrolebindings = crbs_r if not isinstance(crbs_r, Exception) else []
    return {
        "rolebindings": [_format_rolebinding(rb) for rb in rolebindings],
        "clusterrolebindings": [_format_rolebinding(crb) for crb in clusterrolebindings],
    }


@app.get("/clusters/{cluster_id:path}/clusterroles")
async def list_clusterroles(cluster_id: str, system: bool = False):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "clusterroles")
    result = []
    for item in items:
        name = item.get("metadata", {}).get("name", "")
        if not system and (name.startswith("system:") or name.startswith("kubeadm:")):
            continue
        rules = item.get("rules", [])
        result.append({"name": name, "rule_count": len(rules)})
    result.sort(key=lambda x: x["name"])
    return {"clusterroles": result}


@app.post("/clusters/{cluster_id:path}/rolebindings")
async def create_rolebinding(cluster_id: str, request: Request,
                             namespace: str = Query(...), token: str = Query("")):
    body = await request.json()
    name = body.get("name", "")
    subject_kind = body.get("subject_kind", "User")   # User|Group|ServiceAccount
    subject_name = body.get("subject_name", "")
    subject_ns = body.get("subject_namespace", namespace)
    role_ref_kind = body.get("role_ref_kind", "ClusterRole")  # ClusterRole|Role
    role_ref_name = body.get("role_ref_name", "")
    cluster_wide = body.get("cluster_wide", False)
    if not name or not subject_name or not role_ref_name:
        raise HTTPException(400, "name, subject_name, role_ref_name required")
    if token:
        try:
            consume_token(token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        client = await _cluster(cluster_id)
        if cluster_wide:
            manifest = {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRoleBinding",
                "metadata": {"name": name},
                "subjects": [{"kind": subject_kind, "name": subject_name,
                               "namespace": subject_ns if subject_kind == "ServiceAccount" else None}],
                "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": role_ref_name},
            }
        else:
            manifest = {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {"name": name, "namespace": namespace},
                "subjects": [{"kind": subject_kind, "name": subject_name,
                               "namespace": subject_ns if subject_kind == "ServiceAccount" else None}],
                "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": role_ref_kind, "name": role_ref_name},
            }
        # Strip None values from subjects
        for s in manifest["subjects"]:
            if s.get("namespace") is None:
                del s["namespace"]
        await kube_apply(client, manifest)
        return {"ok": True, "name": name}
    return issue_token("create-rolebinding", f"{namespace}/{name}", {
        "subject": f"{subject_kind}/{subject_name}", "role": role_ref_name
    })


@app.delete("/clusters/{cluster_id:path}/rolebindings/{name}")
async def delete_rolebinding(cluster_id: str, name: str,
                             namespace: str = Query(...), token: str = Query(""),
                             cluster_wide: bool = Query(False)):
    if token:
        try:
            consume_token(token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        client = await _cluster(cluster_id)
        kind = "clusterrolebindings" if cluster_wide else "rolebindings"
        await kube_delete(client, kind, name, namespace)
        return {"ok": True}
    return issue_token("delete-rolebinding", f"{namespace}/{name}", {})


@app.get("/clusters/{cluster_id:path}/rolebindings/{name}/explain")
async def explain_rolebinding(cluster_id: str, name: str,
                              namespace: str = Query(...),
                              cluster_wide: bool = Query(False)):
    client = await _cluster(cluster_id)
    kind = "clusterrolebindings" if cluster_wide else "rolebindings"
    rb = await kube_get(client, kind, name, namespace if not cluster_wide else None)
    role_kind = rb.get("roleRef", {}).get("kind", "ClusterRole")
    role_name = rb.get("roleRef", {}).get("name", "")
    subjects = rb.get("subjects", [])
    # Fetch the referenced role rules
    role_kind_key = "clusterroles" if role_kind == "ClusterRole" else "roles"
    try:
        role_obj = await kube_get(client, role_kind_key, role_name,
                                  namespace if role_kind == "Role" else None)
        rules = role_obj.get("rules", [])
    except Exception:
        rules = []

    subjects_str = ", ".join(f"{s.get('kind', '?')} {s.get('name', '?')}" for s in subjects)
    rules_str = json.dumps(rules, indent=2) if rules else "(no rules found)"

    prompt = (
        f"You are explaining Kubernetes RBAC to an ICT admin who came from OpenShift.\n"
        f"A {'ClusterRoleBinding' if cluster_wide else 'RoleBinding'} named '{name}' "
        f"{'(cluster-wide) ' if cluster_wide else f'(namespace: {namespace}) '}"
        f"grants the role '{role_name}' ({role_kind}) to: {subjects_str}.\n\n"
        f"Rules granted:\n{rules_str}\n\n"
        f"Explain in plain English:\n"
        f"1. What resources this grants access to and what operations are allowed\n"
        f"2. Whether this binding is too permissive (security risk)\n"
        f"3. The OpenShift equivalent of this access (if any)\n"
        f"4. A recommendation: keep as-is, narrow the scope, or remove"
    )

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=60) as http:
                resp = await http.post(LLM_GATEWAY_URL + "/chat", json={"prompt": prompt})
                if resp.status_code == 200:
                    text = resp.json().get("text", "")
                    yield f"data: {json.dumps({'text': text})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': f'LLM returned {resp.status_code}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/clusters/{cluster_id:path}/pvcs")
async def list_pvcs(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    pvcs = await kube_list(client, "pvcs", namespace or None)
    pvs = await kube_list(client, "pvs")
    scs = await kube_list(client, "storageclasses")
    return {
        "pvcs": [_format_pvc(p) for p in pvcs],
        "pvs": [_format_generic(p) for p in pvs],
        "storageclasses": [_format_generic(s) for s in scs],
    }


@app.get("/clusters/{cluster_id:path}/pod-connections")
async def pod_connections(cluster_id: str, namespace: str = ""):
    """
    Return pod connection graph: pods with network bytes, service→endpoint edges,
    caller inference from K8s env vars, NetworkPolicy coverage, and security recommendations.
    """
    client = await _cluster(cluster_id)
    ns = namespace or None

    pods_raw, svcs_raw, eps_raw, netpols_raw, nodes_raw = await asyncio.gather(
        kube_list(client, "pods", ns),
        kube_list(client, "services", ns),
        kube_list(client, "endpoints", ns),
        kube_list(client, "networkpolicies", ns),
        kube_list(client, "nodes"),
    )

    # NetworkPolicy coverage per pod
    netpol_index: list[tuple[str, dict, str]] = []  # (ns, matchLabels, name)
    for np in netpols_raw:
        np_ns = np["metadata"]["namespace"]
        sel = np["spec"].get("podSelector", {}).get("matchLabels") or {}
        netpol_index.append((np_ns, sel, np["metadata"]["name"]))

    def _pod_netpol(pod_ns: str, pod_labels: dict) -> tuple[bool, str]:
        for np_ns, sel, np_name in netpol_index:
            if np_ns != pod_ns:
                continue
            if not sel or all(pod_labels.get(k) == v for k, v in sel.items()):
                return True, np_name
        return False, ""

    # Kubelet network stats
    pod_net: dict[str, dict] = {}

    async def _node_net(node_name: str):
        try:
            r = await client.get(f"/api/v1/nodes/{node_name}/proxy/stats/summary", timeout=5.0)
            if r.status_code != 200:
                return
            for ps in r.json().get("pods", []):
                ref = ps.get("podRef", {})
                key = f"{ref.get('namespace','')}/{ref.get('name','')}"
                net = ps.get("network") or {}
                pod_net[key] = {"rx_bytes": net.get("rxBytes", 0), "tx_bytes": net.get("txBytes", 0)}
        except Exception:
            pass

    await asyncio.gather(*[_node_net(n["metadata"]["name"]) for n in nodes_raw])

    # Build pod data
    pod_data = []
    for pod in pods_raw:
        meta = pod["metadata"]
        spec = pod.get("spec", {})
        status = pod.get("status", {})
        pod_ns = meta.get("namespace", "")
        pod_name = meta.get("name", "")
        pod_labels = meta.get("labels") or {}
        phase = status.get("phase", "")

        has_netpol, netpol_name = _pod_netpol(pod_ns, pod_labels)
        net = pod_net.get(f"{pod_ns}/{pod_name}", {})

        # Infer service references from env vars (K8s injects {SVC}_SERVICE_HOST)
        env_svc_refs: set[str] = set()
        for c in spec.get("containers", []):
            for env in c.get("env") or []:
                n = env.get("name", "")
                if n.endswith("_SERVICE_HOST") and env.get("value"):
                    # Convert UPPER_SNAKE to lower-kebab
                    svc_guess = n[: -len("_SERVICE_HOST")].lower().replace("_", "-")
                    env_svc_refs.add(svc_guess)

        pod_data.append({
            "name": pod_name,
            "namespace": pod_ns,
            "labels": pod_labels,
            "ip": status.get("podIP", ""),
            "node": spec.get("nodeName", ""),
            "phase": phase,
            "has_netpol": has_netpol,
            "netpol_name": netpol_name,
            "env_svc_refs": list(env_svc_refs),
            "rx_bytes": net.get("rx_bytes", 0),
            "tx_bytes": net.get("tx_bytes", 0),
        })

    # Endpoint map: (ns, svc_name) → [{name, ns, port}]
    ep_map: dict[tuple, list] = {}
    for ep in eps_raw:
        ep_ns = ep["metadata"]["namespace"]
        ep_name = ep["metadata"]["name"]
        pod_eps = []
        for subset in ep.get("subsets") or []:
            ports = [p.get("port") for p in subset.get("ports") or []]
            for addr in subset.get("addresses") or []:
                ref = addr.get("targetRef") or {}
                if ref.get("kind") == "Pod":
                    for port in ports:
                        pod_eps.append({
                            "name": ref.get("name", ""),
                            "namespace": ref.get("namespace", ep_ns),
                            "port": port,
                        })
        ep_map[(ep_ns, ep_name)] = pod_eps

    # Build connections (service nodes + caller inference)
    connections = []
    for svc in svcs_raw:
        svc_ns = svc["metadata"]["namespace"]
        svc_name = svc["metadata"]["name"]
        spec = svc.get("spec", {})
        svc_type = spec.get("type", "ClusterIP")
        ports = [{"port": p.get("port"), "protocol": p.get("protocol", "TCP"),
                  "target_port": str(p.get("targetPort", ""))}
                 for p in spec.get("ports") or []]
        endpoints = ep_map.get((svc_ns, svc_name), [])[:12]

        # Callers: pods that have this service in env refs
        callers = [
            {"name": p["name"], "namespace": p["namespace"]}
            for p in pod_data
            if svc_name in p["env_svc_refs"] and p["namespace"] == svc_ns
        ][:8]

        connections.append({
            "service_name": svc_name,
            "service_namespace": svc_ns,
            "service_type": svc_type,
            "cluster_ip": spec.get("clusterIP", ""),
            "ports": ports[:4],
            "endpoints": endpoints,
            "callers": callers,
        })

    # Security recommendations
    recs = []
    unprotected = [p for p in pod_data if not p["has_netpol"] and p["phase"] == "Running"]
    if unprotected:
        recs.append({
            "severity": "high",
            "type": "no_netpol",
            "message": f"{len(unprotected)} running pod(s) have no NetworkPolicy — all ingress/egress is unrestricted",
            "targets": [f"{p['namespace']}/{p['name']}" for p in unprotected[:6]],
        })

    for np in netpols_raw:
        spec = np["spec"]
        np_ns = np["metadata"]["namespace"]
        np_name = np["metadata"]["name"]
        pod_sel = spec.get("podSelector", {})

        # Ingress rule with no `from` = allow all sources
        for rule in spec.get("ingress") or []:
            if rule is not None and not rule.get("from"):
                # Generate a fixed policy: same podSelector + policyTypes, but add a restrictive ingress
                fix_lines = [
                    "apiVersion: networking.k8s.io/v1",
                    "kind: NetworkPolicy",
                    "metadata:",
                    f"  name: {np_name}",
                    f"  namespace: {np_ns}",
                    "  annotations:",
                    "    mco/fix: restricted-ingress",
                    "spec:",
                    "  podSelector:",
                ]
                ml = (pod_sel or {}).get("matchLabels", {})
                if ml:
                    fix_lines.append("    matchLabels:")
                    for k, v in ml.items():
                        fix_lines.append(f"      {k}: {v}")
                else:
                    fix_lines.append("    matchLabels: {}")
                fix_lines += [
                    "  policyTypes:",
                    "  - Ingress",
                    "  ingress:",
                    "  - from:",
                    "    - podSelector: {}  # restrict: replace with specific podSelector/namespaceSelector",
                    "    ports:",
                    "    - protocol: TCP",
                    "      port: 80  # adjust to your actual service port",
                ]
                fix_yaml = "\n".join(fix_lines)
                recs.append({
                    "severity": "medium",
                    "type": "wide_open_ingress",
                    "message": f"NetworkPolicy {np_name} in {np_ns} allows ingress from ALL sources (no `from` selector)",
                    "targets": [f"{np_ns}/{np_name}"],
                    "fix_yaml": fix_yaml,
                    "fix_namespace": np_ns,
                })
                break
        # Egress rule with no `to` = allow all destinations
        for rule in spec.get("egress") or []:
            if rule is not None and not rule.get("to"):
                fix_lines = [
                    "apiVersion: networking.k8s.io/v1",
                    "kind: NetworkPolicy",
                    "metadata:",
                    f"  name: {np_name}",
                    f"  namespace: {np_ns}",
                    "  annotations:",
                    "    mco/fix: restricted-egress",
                    "spec:",
                    "  podSelector:",
                ]
                ml = (pod_sel or {}).get("matchLabels", {})
                if ml:
                    fix_lines.append("    matchLabels:")
                    for k, v in ml.items():
                        fix_lines.append(f"      {k}: {v}")
                else:
                    fix_lines.append("    matchLabels: {}")
                fix_lines += [
                    "  policyTypes:",
                    "  - Egress",
                    "  egress:",
                    "  - to:",
                    "    - podSelector: {}  # restrict: replace with specific podSelector/namespaceSelector",
                    "    ports:",
                    "    - protocol: TCP",
                    "      port: 53",
                    "    - protocol: UDP",
                    "      port: 53",
                ]
                fix_yaml = "\n".join(fix_lines)
                recs.append({
                    "severity": "low",
                    "type": "wide_open_egress",
                    "message": f"NetworkPolicy {np_name} in {np_ns} allows egress to ALL destinations (no `to` selector)",
                    "targets": [f"{np_ns}/{np_name}"],
                    "fix_yaml": fix_yaml,
                    "fix_namespace": np_ns,
                })
                break

    for conn in connections:
        if conn["service_type"] in ("NodePort", "LoadBalancer") and conn["endpoints"]:
            ep_names = {(e["name"], e["namespace"]) for e in conn["endpoints"]}
            unguarded = [p for p in pod_data if (p["name"], p["namespace"]) in ep_names and not p["has_netpol"]]
            if unguarded:
                recs.append({
                    "severity": "high",
                    "type": "exposed_no_netpol",
                    "message": (
                        f"Service {conn['service_name']} ({conn['service_type']}) exposes pods "
                        f"that have no NetworkPolicy — direct external access to unprotected pods"
                    ),
                    "targets": [f"{p['namespace']}/{p['name']}" for p in unguarded[:4]],
                })

    # Suggest block rules for inferred callers that cross namespaces
    cross_ns = []
    for conn in connections:
        for caller in conn["callers"]:
            if caller["namespace"] != conn["service_namespace"]:
                cross_ns.append(f"{caller['namespace']}/{caller['name']} → {conn['service_namespace']}/{conn['service_name']}")
    if cross_ns:
        recs.append({
            "severity": "low",
            "type": "cross_namespace_traffic",
            "message": f"{len(cross_ns)} inferred cross-namespace service call(s) — consider adding namespace-scoped NetworkPolicies",
            "targets": cross_ns[:5],
        })

    # ── Generate suggested NetworkPolicy YAMLs for unprotected pods ──────────────
    def _pick_selector_labels(pod_labels: dict) -> dict:
        """Return the most specific stable labels suitable for podSelector."""
        preferred = [
            "app.kubernetes.io/name", "app.kubernetes.io/component",
            "app", "name", "component",
        ]
        sel: dict = {}
        for k in preferred:
            if k in pod_labels:
                sel[k] = pod_labels[k]
                break
        # Add version/instance if present alongside the primary key
        if sel:
            for k in ("app.kubernetes.io/instance", "app.kubernetes.io/version", "version"):
                if k in pod_labels and len(sel) < 3:
                    sel[k] = pod_labels[k]
        return sel or {k: v for k, v in list(pod_labels.items())[:2] if not k.startswith("pod-template")}

    def _yaml_indent(obj, indent: int = 0) -> str:
        """Minimal YAML serialiser (subset: dicts, lists, scalars)."""
        pad = "  " * indent
        if isinstance(obj, dict):
            if not obj:
                return "{}"
            lines = []
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"{pad}{k}:")
                    lines.append(_yaml_indent(v, indent + 1))
                else:
                    lines.append(f"{pad}{k}: {v}")
            return "\n".join(lines)
        if isinstance(obj, list):
            if not obj:
                return f"{pad}[]"
            lines = []
            for item in obj:
                if isinstance(item, dict):
                    first = True
                    for k, v in item.items():
                        prefix = f"{pad}- " if first else f"{pad}  "
                        if isinstance(v, (dict, list)):
                            lines.append(f"{prefix}{k}:")
                            lines.append(_yaml_indent(v, indent + 2 if first else indent + 1))
                        else:
                            lines.append(f"{prefix}{k}: {v}")
                        first = False
                else:
                    lines.append(f"{pad}- {item}")
            return "\n".join(lines)
        return f"{pad}{obj}"

    def _build_netpol_yaml(pod: dict, connections: list) -> str:
        pod_ns = pod["namespace"]
        pod_name = pod["name"]
        pod_labels = pod.get("labels") or {}
        sel_labels = _pick_selector_labels(pod_labels)

        # Services this pod is an endpoint of (ingress ports)
        ingress_rules: list[dict] = []
        egress_rules: list[dict] = []

        for conn in connections:
            if conn["service_namespace"] != pod_ns:
                continue
            # Is this pod an endpoint of this service?
            is_endpoint = any(
                ep["name"] == pod_name and ep["namespace"] == pod_ns
                for ep in conn["endpoints"]
            )
            if is_endpoint:
                # Build ingress rule: allow from known callers (same ns)
                from_selectors = []
                for caller in conn["callers"]:
                    caller_labels = next(
                        (p["labels"] for p in pod_data if p["name"] == caller["name"] and p["namespace"] == caller["namespace"]),
                        {},
                    )
                    caller_sel = _pick_selector_labels(caller_labels)
                    if caller_sel:
                        if caller["namespace"] == pod_ns:
                            from_selectors.append({"podSelector": {"matchLabels": caller_sel}})
                        else:
                            from_selectors.append({
                                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": caller["namespace"]}},
                                "podSelector": {"matchLabels": caller_sel},
                            })
                ports_list = [
                    {"port": pt["port"], "protocol": pt.get("protocol", "TCP")}
                    for pt in conn["ports"] if pt.get("port")
                ]
                rule: dict = {}
                if from_selectors:
                    rule["from"] = from_selectors
                if ports_list:
                    rule["ports"] = ports_list
                ingress_rules.append(rule)

            # Does this pod call this service (egress)?
            is_caller = any(
                c["name"] == pod_name and c["namespace"] == pod_ns
                for c in conn["callers"]
            )
            if is_caller:
                # Build egress rule to the service's endpoint pods
                svc_sel = next(
                    (s.get("spec", {}).get("selector") for s in svcs_raw
                     if s["metadata"]["name"] == conn["service_name"]
                     and s["metadata"]["namespace"] == conn["service_namespace"]),
                    None,
                )
                to_sel: dict = {}
                if svc_sel:
                    to_sel["podSelector"] = {"matchLabels": svc_sel}
                    if conn["service_namespace"] != pod_ns:
                        to_sel["namespaceSelector"] = {"matchLabels": {"kubernetes.io/metadata.name": conn["service_namespace"]}}
                ports_list = [
                    {"port": pt["port"], "protocol": pt.get("protocol", "TCP")}
                    for pt in conn["ports"] if pt.get("port")
                ]
                eg_rule: dict = {}
                if to_sel:
                    eg_rule["to"] = [to_sel]
                if ports_list:
                    eg_rule["ports"] = ports_list
                egress_rules.append(eg_rule)

        # Always allow DNS egress
        egress_rules.append({"ports": [{"port": 53, "protocol": UDP}, {"port": 53, "protocol": TCP}]})  # type: ignore[name-defined]

        # Render YAML — use yaml.dump to properly quote label values with special chars
        policy_name = f"allow-{pod_name[:40]}"
        doc: dict = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": policy_name, "namespace": pod_ns},
            "spec": {
                "podSelector": {"matchLabels": sel_labels} if sel_labels else {"matchLabels": {}},
                "policyTypes": ["Ingress", "Egress"],
            },
        }

        if ingress_rules:
            doc["spec"]["ingress"] = ingress_rules
        else:
            doc["spec"]["ingress"] = []

        if egress_rules:
            doc["spec"]["egress"] = egress_rules

        return _yaml.dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Attach generated policies to unprotected running pods
    # (define string constants for protocol references used above)
    UDP = "UDP"; TCP = "TCP"  # noqa: F841

    generated_netpols = []
    for pod in pod_data:
        if not pod["has_netpol"] and pod["phase"] == "Running":
            try:
                yaml_str = _build_netpol_yaml(pod, connections)
            except Exception:
                yaml_str = f"# Could not generate policy for {pod['namespace']}/{pod['name']}"
            generated_netpols.append({
                "pod_name": pod["name"],
                "namespace": pod["namespace"],
                "selector_labels": _pick_selector_labels(pod.get("labels") or {}),
                "yaml": yaml_str,
            })

    running_pods = [p for p in pod_data if p["phase"] == "Running"]
    return {
        "pods": pod_data,
        "connections": connections,
        "recommendations": recs,
        "generated_netpols": generated_netpols,
        "summary": {
            "total_pods": len(pod_data),
            "running_pods": len(running_pods),
            "protected_pods": sum(1 for p in running_pods if p["has_netpol"]),
            "total_services": len(connections),
            "total_recommendations": len(recs),
        },
    }


@app.post("/clusters/{cluster_id:path}/netpol/explain")
async def explain_netpol(cluster_id: str, body: dict):
    """
    Stream an LLM explanation of a NetworkPolicy YAML in plain language.
    Body: { "yaml": "<yaml string>", "pod_name"?: "...", "namespace"?: "..." }
    Returns SSE: data: {"text": "..."} ... data: {"done": true}
    """
    raw_yaml = body.get("yaml", "").strip()
    pod_name = body.get("pod_name", "")
    namespace = body.get("namespace", "")
    if not raw_yaml:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="yaml is required")

    context = f" for pod `{namespace}/{pod_name}`" if pod_name else ""

    prompt = (
        f"You are a Kubernetes security expert. Explain the following NetworkPolicy{context} "
        f"in clear, plain English for an operator who understands Kubernetes but is not a networking expert.\n\n"
        f"NetworkPolicy YAML:\n```yaml\n{raw_yaml}\n```\n\n"
        f"Your explanation must cover:\n"
        f"1. **What traffic is ALLOWED in** (ingress): which pods/namespaces can reach this pod, on which ports\n"
        f"2. **What traffic is ALLOWED out** (egress): where this pod can send traffic, on which ports\n"
        f"3. **What is BLOCKED** by default (anything not explicitly allowed)\n"
        f"4. **Security posture**: is this policy tight, overly permissive, or has any risks? One short sentence.\n"
        f"5. **One action** the operator should consider to improve it (if any).\n\n"
        f"Be concise — use bullet points. Avoid restating the YAML literally; translate it to intent."
    )

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=60) as http:
                resp = await http.post(LLM_GATEWAY_URL + "/chat", json={"prompt": prompt})
                if resp.status_code == 200:
                    text = resp.json().get("text", "")
                    yield f"data: {json.dumps({'text': text})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': f'LLM returned {resp.status_code}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/clusters/{cluster_id:path}/netpol/apply")
async def apply_netpol(cluster_id: str, body: dict):
    """
    Apply a NetworkPolicy YAML to the cluster.
    Body: { "yaml": "<yaml string>", "namespace": "<ns>" }
    Creates the resource if it doesn't exist, replaces it if it does.
    """
    import yaml as _yaml
    raw_yaml = body.get("yaml", "")
    namespace = body.get("namespace", "")
    if not raw_yaml or not namespace:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="yaml and namespace are required")

    try:
        obj = _yaml.safe_load(raw_yaml)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    name = obj.get("metadata", {}).get("name")
    if not name:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="NetworkPolicy YAML must have metadata.name")

    client = await _cluster(cluster_id)
    url = f"/apis/networking.k8s.io/v1/namespaces/{namespace}/networkpolicies"

    # Check if already exists
    get_resp = await client.get(f"{url}/{name}")
    if get_resp.status_code == 200:
        existing = get_resp.json()
        obj.setdefault("metadata", {})["resourceVersion"] = existing["metadata"]["resourceVersion"]
        resp = await client.put(f"{url}/{name}", json=obj)
        action = "updated"
    else:
        resp = await client.post(url, json=obj)
        action = "created"

    if resp.status_code not in (200, 201):
        from fastapi import HTTPException
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    result = resp.json()
    return {
        "action": action,
        "name": result["metadata"]["name"],
        "namespace": result["metadata"]["namespace"],
        "uid": result["metadata"].get("uid"),
    }


@app.get("/clusters/{cluster_id:path}/pvc-usage")
async def pvc_usage(cluster_id: str):
    """Return per-PVC used/capacity bytes from kubelet stats summaries."""
    client = await _cluster(cluster_id)
    nodes_resp = await client.get("/api/v1/nodes")
    nodes = nodes_resp.json().get("items", []) if nodes_resp.status_code == 200 else []
    usage: dict[str, dict] = {}

    async def _node_stats(node_name: str):
        try:
            r = await client.get(f"/api/v1/nodes/{node_name}/proxy/stats/summary", timeout=5.0)
            if r.status_code != 200:
                return
            for pod in r.json().get("pods", []):
                for vol in pod.get("volume", []):
                    pvc_ref = vol.get("pvcRef")
                    if not pvc_ref:
                        continue
                    key = f"{pvc_ref.get('namespace', '')}/{pvc_ref.get('name', '')}"
                    usage[key] = {
                        "used_bytes": vol.get("usedBytes", 0),
                        "capacity_bytes": vol.get("capacityBytes", 0),
                    }
        except Exception:
            pass

    await asyncio.gather(*[_node_stats(n["metadata"]["name"]) for n in nodes])
    return {"usage": usage}


@app.get("/clusters/{cluster_id:path}/pod-metrics")
async def pod_metrics(cluster_id: str, namespace: str = ""):
    """
    Return live CPU and memory utilization per pod from kubelet stats summaries,
    enriched with per-pod resource limits from the pod spec.
    Response: { metrics: { "ns/name": { cpu_cores, mem_bytes, cpu_limit_cores, mem_limit_bytes, containers: [...] } } }
    """
    def _cpu_m_to_cores(cpu: str) -> float:
        cpu = str(cpu)
        if cpu.endswith("m"):
            return float(cpu[:-1]) / 1000
        try:
            return float(cpu)
        except ValueError:
            return 0.0

    def _mem_str_to_bytes(mem: str) -> int:
        mem = str(mem)
        try:
            if mem.endswith("Ki"): return int(mem[:-2]) * 1024
            if mem.endswith("Mi"): return int(mem[:-2]) * 1024 * 1024
            if mem.endswith("Gi"): return int(mem[:-2]) * 1024 * 1024 * 1024
            if mem.endswith("Ti"): return int(mem[:-2]) * 1024 ** 4
            return int(mem)
        except ValueError:
            return 0

    client = await _cluster(cluster_id)
    nodes_resp, pods_resp = await asyncio.gather(
        client.get("/api/v1/nodes"),
        client.get(f"/api/v1/{'namespaces/' + namespace + '/' if namespace else ''}pods"),
    )
    nodes = nodes_resp.json().get("items", []) if nodes_resp.status_code == 200 else []
    pods_raw = pods_resp.json().get("items", []) if pods_resp.status_code == 200 else []

    # Build limit lookup: ns/name → {cpu_limit_cores, mem_limit_bytes}
    limits: dict[str, dict] = {}
    for pod in pods_raw:
        meta = pod.get("metadata", {})
        key = f"{meta.get('namespace', '')}/{meta.get('name', '')}"
        cpu_lim = 0.0; mem_lim = 0
        for c in pod.get("spec", {}).get("containers", []):
            lims = c.get("resources", {}).get("limits", {})
            cpu_lim += _cpu_m_to_cores(lims.get("cpu", "0"))
            mem_lim += _mem_str_to_bytes(lims.get("memory", "0"))
        limits[key] = {"cpu_limit_cores": round(cpu_lim, 4), "mem_limit_bytes": mem_lim}

    metrics: dict[str, dict] = {}

    async def _node_stats(node_name: str):
        try:
            r = await client.get(f"/api/v1/nodes/{node_name}/proxy/stats/summary", timeout=5.0)
            if r.status_code != 200:
                return
            for pod in r.json().get("pods", []):
                pod_ns = pod.get("podRef", {}).get("namespace", "")
                pod_name = pod.get("podRef", {}).get("name", "")
                if not pod_name:
                    continue
                if namespace and pod_ns != namespace:
                    continue
                key = f"{pod_ns}/{pod_name}"
                cpu_cores = 0.0; mem_bytes = 0
                containers = []
                for c in pod.get("containers", []):
                    c_cpu = (c.get("cpu") or {}).get("usageNanoCores", 0) / 1e9
                    c_mem = (c.get("memory") or {}).get("workingSetBytes", 0)
                    cpu_cores += c_cpu; mem_bytes += c_mem
                    containers.append({"name": c.get("name", ""), "cpu_cores": round(c_cpu, 4), "mem_bytes": c_mem})
                lim = limits.get(key, {})
                metrics[key] = {
                    "cpu_cores": round(cpu_cores, 4),
                    "mem_bytes": mem_bytes,
                    "cpu_limit_cores": lim.get("cpu_limit_cores", 0.0),
                    "mem_limit_bytes": lim.get("mem_limit_bytes", 0),
                    "containers": containers,
                }
        except Exception:
            pass

    await asyncio.gather(*[_node_stats(n["metadata"]["name"]) for n in nodes])
    return {"metrics": metrics}


@app.delete("/clusters/{cluster_id:path}/pvcs/{name}")
async def delete_pvc(
    cluster_id: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    if not token:
        return issue_token("delete-pvc", f"{cluster_id}/{namespace}/pvcs/{name}",
                           {"description": f"Delete PVC {name} in namespace {namespace} — irreversible, data may be lost"})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "delete-pvc", cluster_id, namespace, "PersistentVolumeClaim", name)
    client = await _cluster(cluster_id)
    await kube_delete(client, "pvcs", name, namespace)
    return {"ok": True, "name": name, "namespace": namespace}


@app.patch("/clusters/{cluster_id:path}/pvcs/{name}")
async def resize_pvc(cluster_id: str, name: str, body: dict = Body(...),
                     namespace: str = Query("default"), token: str = Query("")):
    """Resize a PVC by patching spec.resources.requests.storage.
    Requires the StorageClass to support volume expansion (allowVolumeExpansion: true).
    Only expansion is allowed (cannot shrink)."""
    new_size = body.get("storage")
    if not new_size:
        raise HTTPException(400, "storage field is required (e.g. '20Gi')")
    if not token:
        return issue_token("resize-pvc", f"{namespace}/{name}", {"storage": new_size})
    consume_token(token)
    client = await _cluster(cluster_id)
    await kube_patch(client, "pvcs", name, namespace,
                     {"spec": {"resources": {"requests": {"storage": new_size}}}})
    return {"ok": True, "name": name, "storage": new_size}


@app.get("/clusters/{cluster_id:path}/configmaps")
async def list_configmaps(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "configmaps", namespace or None)
    return {"configmaps": [_format_configmap(i) for i in items]}


@app.get("/clusters/{cluster_id:path}/configmaps/{name}")
async def get_configmap(cluster_id: str, name: str, namespace: str = "default"):
    client = await _cluster(cluster_id)
    obj = await kube_get(client, "configmaps", name, namespace)
    return _format_configmap(obj)


@app.put("/clusters/{cluster_id:path}/configmaps/{name}")
async def update_configmap(cluster_id: str, name: str, request: Request, namespace: str = "default"):
    body = await request.json()
    data = body.get("data")
    if not isinstance(data, dict):
        raise HTTPException(400, "data must be a JSON object (key→value)")
    user = _user(request)
    client = await _cluster(cluster_id)
    patch = {"data": data}
    result = await kube_patch(client, "configmaps", name, patch, namespace)
    await audit_emit(user, "configmap_update", cluster_id, namespace, "ConfigMap", name)
    return {"status": "updated", "name": result.get("metadata", {}).get("name")}


@app.delete("/clusters/{cluster_id:path}/configmaps/{name}")
async def delete_configmap(
    cluster_id: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    if not token:
        return issue_token("delete-configmap", f"{cluster_id}/{namespace}/configmaps/{name}",
                           {"description": f"Delete ConfigMap {name} in {namespace}"})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "delete-configmap", cluster_id, namespace, "ConfigMap", name)
    client = await _cluster(cluster_id)
    await kube_delete(client, "configmaps", name, namespace)
    return {"ok": True, "name": name, "namespace": namespace}


@app.get("/clusters/{cluster_id:path}/secrets")
async def list_secrets(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "secrets", namespace or None)
    return {"secrets": [_format_secret_item(i) for i in items]}


def _format_secret_item(obj: dict) -> dict:
    meta = obj.get("metadata", {})
    data = obj.get("data", {}) or {}
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "type": obj.get("type", "Opaque"),
        "keys_count": len(data),
        "created_at": meta.get("creationTimestamp", ""),
    }


@app.post("/clusters/{cluster_id:path}/secrets")
async def create_secret(cluster_id: str, request: Request,
                        namespace: str = Query(...), token: str = Query("")):
    body = await request.json()
    name = body.get("name", "")
    secret_type = body.get("type", "Opaque")
    data_plain: dict = body.get("data", {})
    if not name:
        raise HTTPException(400, "name required")
    if not data_plain:
        raise HTTPException(400, "data required")
    if token:
        try:
            consume_token(token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        import base64 as _b64
        encoded = {k: _b64.b64encode(v.encode()).decode() for k, v in data_plain.items()}
        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name, "namespace": namespace},
            "type": secret_type,
            "data": encoded,
        }
        client = await _cluster(cluster_id)
        await kube_apply(client, manifest)
        return {"ok": True, "name": name}
    return issue_token("create-secret", f"{namespace}/{name}", {
        "type": secret_type, "keys": list(data_plain.keys())
    })


@app.patch("/clusters/{cluster_id:path}/secrets/{name}")
async def patch_secret(cluster_id: str, name: str, request: Request,
                       namespace: str = Query(...), token: str = Query("")):
    body = await request.json()
    data_plain: dict = body.get("data", {})
    if not data_plain:
        raise HTTPException(400, "data required")
    if token:
        try:
            consume_token(token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        import base64 as _b64
        encoded = {k: _b64.b64encode(v.encode()).decode() for k, v in data_plain.items()}
        client = await _cluster(cluster_id)
        await kube_patch(client, "secrets", name, namespace, {"data": encoded})
        return {"ok": True}
    return issue_token("patch-secret", f"{namespace}/{name}", {"keys": list(data_plain.keys())})


@app.get("/clusters/{cluster_id:path}/hpa")
async def list_hpa(cluster_id: str, namespace: str = ""):
    client = await _cluster(cluster_id)
    items = await kube_list(client, "hpas", namespace or None)
    return {"hpa": [_format_hpa(i) for i in items]}


@app.patch("/clusters/{cluster_id:path}/hpa/{name}")
async def patch_hpa(cluster_id: str, name: str, body: dict = Body(...),
                    namespace: str = Query("default"), token: str = Query("")):
    """Patch HPA min/max replicas and target CPU utilization."""
    min_r = body.get("min_replicas")
    max_r = body.get("max_replicas")
    target_cpu = body.get("target_cpu_pct")
    if min_r is None and max_r is None and target_cpu is None:
        raise HTTPException(400, "Provide at least one of: min_replicas, max_replicas, target_cpu_pct")
    if not token:
        return issue_token("patch-hpa", f"{namespace}/{name}", {"min_replicas": min_r, "max_replicas": max_r, "target_cpu_pct": target_cpu})
    consume_token(token)
    client = await _cluster(cluster_id)
    spec: dict = {}
    if min_r is not None: spec["minReplicas"] = int(min_r)
    if max_r is not None: spec["maxReplicas"] = int(max_r)
    metrics_patch = []
    if target_cpu is not None:
        metrics_patch = [{"type": "Resource", "resource": {"name": "cpu", "target": {"type": "Utilization", "averageUtilization": int(target_cpu)}}}]
    patch_body: dict = {"spec": {**spec, **({"metrics": metrics_patch} if metrics_patch else {})}}
    await kube_patch(client, "hpas", name, namespace, patch_body)
    return {"ok": True}


@app.post("/clusters/{cluster_id:path}/hpa")
async def create_hpa(cluster_id: str, body: dict = Body(...),
                     namespace: str = Query("default"), token: str = Query("")):
    """Create a new HorizontalPodAutoscaler."""
    hpa_name = body.get("name")
    target_kind = body.get("target_kind", "Deployment")
    target_name = body.get("target_name")
    min_r = body.get("min_replicas", 1)
    max_r = body.get("max_replicas")
    target_cpu = body.get("target_cpu_pct", 80)
    if not hpa_name or not target_name or not max_r:
        raise HTTPException(400, "name, target_name, and max_replicas are required")
    if not token:
        return issue_token("create-hpa", f"{namespace}/{hpa_name}", {"target_kind": target_kind, "target_name": target_name, "min": min_r, "max": max_r, "cpu": target_cpu})
    consume_token(token)
    client = await _cluster(cluster_id)
    manifest = {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": {"name": hpa_name, "namespace": namespace},
        "spec": {
            "scaleTargetRef": {"apiVersion": "apps/v1", "kind": target_kind, "name": target_name},
            "minReplicas": int(min_r),
            "maxReplicas": int(max_r),
            "metrics": [{"type": "Resource", "resource": {"name": "cpu", "target": {"type": "Utilization", "averageUtilization": int(target_cpu)}}}],
        },
    }
    await kube_apply(client, manifest)
    return {"ok": True, "name": hpa_name}


@app.delete("/clusters/{cluster_id:path}/hpa/{name}")
async def delete_hpa(cluster_id: str, name: str,
                     namespace: str = Query("default"), token: str = Query("")):
    if not token:
        return issue_token("delete-hpa", f"{namespace}/{name}", {})
    consume_token(token)
    client = await _cluster(cluster_id)
    await kube_delete(client, "hpas", name, namespace)
    return {"ok": True}


@app.get("/clusters/{cluster_id:path}/events")
async def list_events(cluster_id: str, namespace: str = "", severity: str = "", pod: str = ""):
    client = await _cluster(cluster_id)
    field_selector = f"involvedObject.name={pod}" if pod else None
    events = await kube_list(client, "events", namespace or None, field_selector=field_selector)
    events.sort(key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "", reverse=True)
    formatted = [_format_event(e) for e in events]
    if severity:
        formatted = [e for e in formatted if e["type"].lower() == severity.lower()]
    return {"events": formatted[:500]}


@app.get("/clusters/{cluster_id:path}/kubeconfig")
async def download_kubeconfig(cluster_id: str, request: Request):
    """Download the admin kubeconfig for a cluster (audited)."""
    user = _user(request)
    ns, name = cluster_id.split("/", 1)
    await audit_emit(user, "download-kubeconfig", cluster_id, ns, "Cluster", name)
    supervisor = await get_supervisor_client()
    url = f"/api/v1/namespaces/{ns}/secrets/{name}-kubeconfig"
    resp = await supervisor.get(url)
    if resp.status_code == 404:
        raise HTTPException(404, f"Kubeconfig secret not found for {cluster_id}")
    resp.raise_for_status()
    import base64
    kc_b64 = resp.json().get("data", {}).get("value", "")
    kc_yaml = base64.b64decode(kc_b64).decode() if kc_b64 else ""
    return StreamingResponse(
        iter([kc_yaml]),
        media_type="application/x-yaml",
        headers={"Content-Disposition": f"attachment; filename={name}.yaml"},
    )


# ── Phase B: Actions ──────────────────────────────────────────────────────────

@app.get("/clusters/{cluster_id:path}/pods/{pod}/logs")
async def pod_logs(
    cluster_id: str,
    pod: str,
    namespace: str = Query(...),
    container: str = "",
    follow: bool = False,
    tail_lines: int = 200,
):
    client = await _cluster(cluster_id)

    async def _stream() -> AsyncGenerator[bytes, None]:
        base_params: dict = {"tailLines": tail_lines}
        if container:
            base_params["container"] = container
        try:
            if follow:
                async with client.stream(
                    "GET",
                    f"/api/v1/namespaces/{namespace}/pods/{pod}/log",
                    params={"follow": "true", **base_params},
                ) as resp:
                    async for line in resp.aiter_lines():
                        yield f"data: {json.dumps({'line': line})}\n\n".encode()
                        await asyncio.sleep(0)
            else:
                resp = await client.get(
                    f"/api/v1/namespaces/{namespace}/pods/{pod}/log",
                    params=base_params,
                )
                for line in resp.text.splitlines():
                    yield f"data: {json.dumps({'line': line})}\n\n".encode()
            yield b"data: {\"done\": true}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n".encode()

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/clusters/{cluster_id:path}/deployments/{name}/scale")
async def scale_deployment(
    cluster_id: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    body = await request.json()
    replicas = body.get("replicas")
    if replicas is None:
        raise HTTPException(400, "replicas required")
    if not isinstance(replicas, int) or replicas < 0:
        raise HTTPException(400, "replicas must be a non-negative integer")

    action_desc = f"Scale deployment {name} in {namespace}/{cluster_id} to {replicas} replicas"
    if not token:
        return issue_token("scale", f"{cluster_id}/{namespace}/deployments/{name}",
                           {"replicas": replicas, "description": action_desc})

    _consume(token)
    user = _user(request)
    await audit_emit(user, "scale", cluster_id, namespace, "Deployment", name, {"replicas": replicas})

    client = await _cluster(cluster_id)
    await kube_patch(client, "deployments", name, namespace, {"spec": {"replicas": replicas}})
    return {"ok": True, "replicas": replicas}


_RESTARTABLE = {"deployments", "statefulsets", "daemonsets"}


@app.get("/clusters/{cluster_id:path}/deployments/{name}/history")
async def deployment_history(cluster_id: str, name: str, namespace: str = Query(...)):
    """Return rollout revision history for a deployment (via its ReplicaSets)."""
    client = await _cluster(cluster_id)
    # Fetch the deployment to get its selector
    dep = await kube_get(client, "deployments", name, namespace)
    selector = dep.get("spec", {}).get("selector", {}).get("matchLabels", {})
    label_sel = ",".join(f"{k}={v}" for k, v in selector.items())
    # List ReplicaSets matching the deployment's selector
    all_rs = await kube_list(client, "replicasets", namespace, label_selector=label_sel)
    # Filter to RSes owned by this deployment
    revisions = []
    for rs in all_rs:
        meta = rs.get("metadata", {})
        owners = meta.get("ownerReferences", [])
        if not any(o.get("name") == name and o.get("kind") == "Deployment" for o in owners):
            continue
        annotations = meta.get("annotations", {})
        revision = int(annotations.get("deployment.kubernetes.io/revision", "0"))
        spec = rs.get("spec", {})
        status = rs.get("status", {})
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        revisions.append({
            "revision": revision,
            "name": meta.get("name", ""),
            "created_at": meta.get("creationTimestamp", ""),
            "replicas": spec.get("replicas", 0),
            "ready_replicas": status.get("readyReplicas", 0),
            "images": [c.get("image", "") for c in containers],
            "change_cause": annotations.get("kubernetes.io/change-cause", ""),
        })
    revisions.sort(key=lambda r: r["revision"], reverse=True)
    return {"revisions": revisions}


@app.post("/clusters/{cluster_id:path}/deployments/{name}/rollback")
async def rollback_deployment(
    cluster_id: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    """Roll back a deployment to a specific revision (by ReplicaSet name)."""
    body = await request.json()
    rs_name = body.get("rs_name")
    if not rs_name:
        raise HTTPException(400, "rs_name required")

    action_desc = f"Roll back Deployment {name} in {namespace} to revision {rs_name}"
    if not token:
        return issue_token("rollback", f"{cluster_id}/{namespace}/deployments/{name}",
                           {"rs_name": rs_name, "description": action_desc})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "rollback", cluster_id, namespace, "Deployment", name, {"rs_name": rs_name})

    client = await _cluster(cluster_id)
    rs = await kube_get(client, "replicasets", rs_name, namespace)
    pod_template = rs.get("spec", {}).get("template", {})
    # Strip immutable fields from pod template metadata
    pod_template.get("metadata", {}).pop("creationTimestamp", None)
    patch = {"spec": {"template": pod_template}}
    await kube_patch(client, "deployments", name, namespace, patch)
    return {"ok": True, "rolled_back_to": rs_name}


@app.patch("/clusters/{cluster_id:path}/workloads/{kind}/{name}/resources")
async def patch_workload_resources(
    cluster_id: str,
    kind: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    """Patch resource requests/limits for one or more containers. Confirms before applying."""
    body = await request.json()
    containers = body.get("containers", [])
    if not containers:
        raise HTTPException(400, "containers required")

    container_names = ", ".join(c["name"] for c in containers)
    action_desc = f"Update resource limits for {container_names} in {kind}/{name} ({namespace})"
    if not token:
        return issue_token("patch-resources", f"{cluster_id}/{namespace}/{kind}/{name}",
                           {"containers": containers, "description": action_desc})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "patch-resources", cluster_id, namespace, kind, name, {"containers": containers})

    client = await _cluster(cluster_id)
    patch_containers = []
    for c in containers:
        entry: dict = {"name": c["name"]}
        resources: dict = {}
        if c.get("requests"):
            resources["requests"] = {k: v for k, v in c["requests"].items() if v}
        if c.get("limits"):
            resources["limits"] = {k: v for k, v in c["limits"].items() if v}
        entry["resources"] = resources
        patch_containers.append(entry)
    patch = {"spec": {"template": {"spec": {"containers": patch_containers}}}}
    await kube_patch(client, kind, name, namespace, patch)
    return {"ok": True}


@app.patch("/clusters/{cluster_id:path}/workloads/{kind}/{name}/env")
async def patch_workload_env(
    cluster_id: str,
    kind: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    """Replace the full env list for a specific container. Confirms before applying."""
    body = await request.json()
    container_name = body.get("container")
    env = body.get("env")
    if not container_name or env is None:
        raise HTTPException(400, "container and env required")

    action_desc = f"Update env vars for container {container_name} in {kind}/{name} ({namespace})"
    if not token:
        return issue_token("patch-env", f"{cluster_id}/{namespace}/{kind}/{name}/{container_name}",
                           {"container": container_name, "env_count": len(env), "description": action_desc})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "patch-env", cluster_id, namespace, kind, name, {"container": container_name})

    client = await _cluster(cluster_id)
    patch = {"spec": {"template": {"spec": {"containers": [{"name": container_name, "env": env}]}}}}
    await kube_patch(client, kind, name, namespace, patch)
    return {"ok": True}


@app.patch("/clusters/{cluster_id:path}/workloads/{kind}/{name}/image")
async def patch_workload_image(
    cluster_id: str,
    kind: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    """Update a specific container's image. Confirms before applying."""
    body = await request.json()
    container_name = body.get("container")
    image = body.get("image", "").strip()
    if not container_name or not image:
        raise HTTPException(400, "container and image required")

    action_desc = f"Update image for {container_name} in {kind}/{name} → {image}"
    if not token:
        return issue_token("patch-image", f"{cluster_id}/{namespace}/{kind}/{name}/{container_name}",
                           {"container": container_name, "image": image, "description": action_desc})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "patch-image", cluster_id, namespace, kind, name,
                     {"container": container_name, "image": image})

    client = await _cluster(cluster_id)
    patch = {"spec": {"template": {"spec": {"containers": [{"name": container_name, "image": image}]}}}}
    await kube_patch(client, kind, name, namespace, patch)
    return {"ok": True, "image": image}


@app.get("/clusters/{cluster_id:path}/workloads/{kind}/{name}/diagnose")
async def diagnose_workload(
    cluster_id: str,
    kind: str,
    name: str,
    namespace: str = Query(...),
    mode: str = Query("diagnose"),
):
    """AI diagnosis or explanation for a workload (Deployment / StatefulSet / DaemonSet)."""
    client = await _cluster(cluster_id)

    workload, events_raw = await asyncio.gather(
        kube_get(client, kind, name, namespace),
        kube_list(client, "events", namespace, field_selector=f"involvedObject.name={name}"),
    )

    spec = workload.get("spec", {})
    status = workload.get("status", {})
    replicas = spec.get("replicas", 0)
    ready = status.get("readyReplicas", 0)
    conditions = status.get("conditions", [])
    containers = spec.get("template", {}).get("spec", {}).get("containers", [])
    images = [{"name": c.get("name"), "image": c.get("image")} for c in containers]
    events_text = "\n".join(
        f"[{e['type']}] {e['reason']}: {e['message']}"
        for e in [_format_event(ev) for ev in events_raw[:20]]
    )

    if mode == "teach":
        prompt = f"""{kind.rstrip('s').title()}: {name} in {namespace}
Replicas: {ready}/{replicas} ready
Conditions: {json.dumps(conditions, indent=2)}
Images: {json.dumps(images)}
Recent events:
{events_text}

Explain this workload's state to an ICT admin who is new to Kubernetes:
1. **What is happening** — current state in plain language
2. **The Kubernetes concept** — what K8s mechanism is involved
3. **Why Kubernetes does this** — the design reason
4. **What to learn next** — one specific concept to study
5. **Hands-on exercise** — one kubectl command to run right now

Use markdown with headers and code blocks."""
    else:
        prompt = f"""{kind.rstrip('s').title()}: {name} in {namespace}/{cluster_id}
Replicas: {ready}/{replicas} ready
Conditions: {json.dumps(conditions, indent=2)}
Images: {json.dumps(images)}
Recent events:
{events_text}

Diagnose the workload state and provide (use markdown):
## Root Cause
(why is this not fully healthy?)

## How to Fix
(concrete kubectl commands or config changes)

## How to Prevent
(best practice recommendation)"""

    async def _stream():
        try:
            async with httpx.AsyncClient(timeout=90.0) as http:
                resp = await http.post(
                    f"{LLM_GATEWAY_URL}/explain",
                    json={
                        "scoring_result": {"readiness_score": 0, "status": "DIAGNOSING", "risk_factors": [], "recommendations": []},
                        "raw_data": {"workload": workload, "events": events_text},
                        "query": prompt,
                        "target": "k8s_workload_diagnosis",
                    },
                )
                if resp.status_code == 200:
                    ct = resp.headers.get("content-type", "")
                    if "event-stream" in ct:
                        for line in resp.text.splitlines():
                            if line.startswith("data:"):
                                yield f"{line}\n\n"
                    else:
                        data = resp.json()
                        explanation = data.get("explanation") or data.get("text", "")
                        yield f"data: {json.dumps({'text': explanation, 'done': True})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': 'LLM unavailable'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


_DIFF_STRIP = {"managedFields", "resourceVersion", "generation", "creationTimestamp",
               "uid", "selfLink", "annotations"}


def _strip_meta(obj: dict) -> dict:
    """Remove server-set noise fields before diffing."""
    if not isinstance(obj, dict):
        return obj
    meta = obj.get("metadata", {})
    clean_meta = {k: v for k, v in meta.items() if k not in _DIFF_STRIP}
    anns = clean_meta.get("annotations", {})
    clean_meta["annotations"] = {
        k: v for k, v in anns.items()
        if k != "kubectl.kubernetes.io/last-applied-configuration"
    }
    if not clean_meta["annotations"]:
        clean_meta.pop("annotations", None)
    result = dict(obj)
    result["metadata"] = clean_meta
    result.pop("status", None)
    return result


@app.get("/clusters/{cluster_id:path}/workloads/{kind}/{name}/diff")
async def workload_diff(cluster_id: str, kind: str, name: str, namespace: str = Query(...)):
    """Return current spec vs last-applied annotation as pretty JSON strings for diffing."""
    client = await _cluster(cluster_id)
    resource = await kube_get(client, kind, name, namespace)
    annotations = resource.get("metadata", {}).get("annotations", {})
    last_applied_raw = annotations.get("kubectl.kubernetes.io/last-applied-configuration")
    if last_applied_raw:
        try:
            last_applied = json.loads(last_applied_raw)
        except Exception:
            last_applied = None
    else:
        last_applied = None
    return {
        "has_annotation": last_applied is not None,
        "current": json.dumps(_strip_meta(resource), indent=2, default=str),
        "last_applied": json.dumps(_strip_meta(last_applied), indent=2, default=str) if last_applied else None,
    }


@app.post("/clusters/{cluster_id:path}/deployments/{name}/restart")
async def restart_deployment(
    cluster_id: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    return await _do_restart(cluster_id, name, "deployments", namespace, request, token)


@app.post("/clusters/{cluster_id:path}/statefulsets/{name}/restart")
async def restart_statefulset(cluster_id: str, name: str, request: Request,
                              namespace: str = Query(...), token: str = Query("")):
    return await _do_restart(cluster_id, name, "statefulsets", namespace, request, token)


@app.post("/clusters/{cluster_id:path}/daemonsets/{name}/restart")
async def restart_daemonset(cluster_id: str, name: str, request: Request,
                            namespace: str = Query(...), token: str = Query("")):
    return await _do_restart(cluster_id, name, "daemonsets", namespace, request, token)


async def _do_restart(cluster_id: str, name: str, kind: str, namespace: str,
                      request: Request, token: str):
    kind_display = kind.rstrip('s').title()
    action_desc = f"Rollout restart {kind_display} {name} in {namespace}/{cluster_id}"
    if not token:
        return issue_token("restart", f"{cluster_id}/{namespace}/{kind}/{name}",
                           {"description": action_desc})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "restart", cluster_id, namespace, kind_display, name)
    client = await _cluster(cluster_id)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    patch = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
    await kube_patch(client, kind, name, namespace, patch)
    return {"ok": True}


@app.post("/clusters/{cluster_id:path}/pods/{pod}/delete")
async def delete_pod(
    cluster_id: str,
    pod: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    action_desc = f"Delete pod {pod} in {namespace}/{cluster_id}"
    if not token:
        return issue_token("delete-pod", f"{cluster_id}/{namespace}/pods/{pod}",
                           {"description": action_desc})

    _consume(token)
    user = _user(request)
    await audit_emit(user, "delete", cluster_id, namespace, "Pod", pod)

    client = await _cluster(cluster_id)
    try:
        await kube_delete(client, "pods", pod, namespace)
    except Exception as e:
        raise HTTPException(500, f"Delete failed: {e}")
    return {"ok": True}


@app.websocket("/clusters/{cluster_id:path}/pods/{pod}/exec")
async def pod_exec_ws(
    websocket: WebSocket,
    cluster_id: str,
    pod: str,
    namespace: str = "default",
    command: str = "/bin/sh",
    container: str = "",
):
    """WebSocket proxy for kubectl exec — streams stdin/stdout/stderr."""
    import ssl as _ssl
    import websockets as _ws

    await websocket.accept()

    # Ensure the cluster client (and info) is cached
    try:
        await _cluster(cluster_id)
    except Exception as e:
        await websocket.send_text(f"[error] Cluster not found: {e}")
        await websocket.close()
        return

    info = get_cluster_parsed_info(cluster_id)
    if not info:
        await websocket.send_text("[error] Cluster info not available — try refreshing the page")
        await websocket.close()
        return

    server: str = info["server"]
    token: str = info.get("token", "")
    ca_data: bytes | None = info.get("ca_data")
    cert_data: bytes | None = info.get("cert_data")
    key_data: bytes | None = info.get("key_data")
    insecure: bool = info.get("insecure", False)

    # Build WebSocket URL
    ws_base = server.replace("https://", "wss://").replace("http://", "ws://")
    cmd_parts = command.split() if " " in command else [command]
    cmd_params = "&".join(f"command={c}" for c in cmd_parts)
    container_param = f"&container={container}" if container else ""
    exec_url = (
        f"{ws_base}/api/v1/namespaces/{namespace}/pods/{pod}/exec"
        f"?{cmd_params}&stdin=1&stdout=1&stderr=1&tty=1{container_param}"
    )

    # Build SSL context
    if insecure:
        ssl_ctx = _ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE
    elif ca_data:
        ssl_ctx = _ssl.create_default_context(cadata=ca_data.decode("utf-8", errors="replace"))
    else:
        ssl_ctx = True  # type: ignore[assignment]

    # Client cert (mutual TLS)
    if cert_data and key_data:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".crt") as cf:
            cf.write(cert_data); cf_name = cf.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".key") as kf:
            kf.write(key_data); kf_name = kf.name
        if isinstance(ssl_ctx, _ssl.SSLContext):
            ssl_ctx.load_cert_chain(cf_name, kf_name)

    extra_headers = {}
    if token:
        extra_headers["Authorization"] = f"Bearer {token}"

    try:
        async with _ws.connect(
            exec_url,
            ssl=ssl_ctx,
            additional_headers=extra_headers,
            subprotocols=["v4.channel.k8s.io"],
            max_size=2**20,
            open_timeout=10,
        ) as k8s_ws:
            async def browser_to_k8s():
                try:
                    async for msg in websocket.iter_bytes():
                        # stdin channel = 0x00 prefix
                        await k8s_ws.send(b"\x00" + msg)
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            async def k8s_to_browser():
                try:
                    async for msg in k8s_ws:
                        data = msg if isinstance(msg, bytes) else msg.encode()
                        if len(data) > 1:
                            channel = data[0]
                            payload = data[1:]
                            if channel in (1, 2):  # stdout, stderr
                                await websocket.send_bytes(payload)
                            elif channel == 3:  # error/status
                                try:
                                    status = json.loads(payload)
                                    if status.get("status") == "Failure":
                                        await websocket.send_text(f"\r\n[error] {status.get('message', 'exec failed')}\r\n")
                                except Exception:
                                    pass
                except Exception:
                    pass

            await asyncio.gather(browser_to_k8s(), k8s_to_browser())

    except Exception as e:
        try:
            await websocket.send_text(f"\r\n[error] Could not connect to pod: {e}\r\n")
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/clusters/{cluster_id:path}/workloads/{kind}/{name}/delete")
async def delete_workload(
    cluster_id: str,
    kind: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    allowed = {"deployments", "statefulsets", "daemonsets", "jobs", "cronjobs"}
    if kind not in allowed:
        raise HTTPException(400, f"kind must be one of {sorted(allowed)}")

    action_desc = f"Delete {kind}/{name} in {namespace}/{cluster_id}"
    if not token:
        return issue_token(f"delete-{kind}", f"{cluster_id}/{namespace}/{kind}/{name}",
                           {"description": action_desc})

    _consume(token)
    user = _user(request)
    await audit_emit(user, "delete", cluster_id, namespace, kind, name)

    client = await _cluster(cluster_id)
    await kube_delete(client, kind, name, namespace)
    return {"ok": True}


@app.post("/clusters/{cluster_id:path}/workloads/{kind}/{name}/edit")
async def edit_workload(
    cluster_id: str,
    kind: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    """Apply a full YAML replacement of a workload (edit-as-YAML)."""
    body = await request.json()
    manifest = body.get("manifest")
    if not manifest:
        raise HTTPException(400, "manifest required")

    action_desc = f"Replace full YAML of {kind}/{name} in {namespace}/{cluster_id}"
    if not token:
        return issue_token(f"edit-{kind}", f"{cluster_id}/{namespace}/{kind}/{name}",
                           {"description": action_desc})

    _consume(token)
    user = _user(request)
    await audit_emit(user, "edit", cluster_id, namespace, kind, name)

    client = await _cluster(cluster_id)
    result = await kube_apply(client, manifest)
    return {"ok": True, "result": result}


@app.post("/clusters/{cluster_id:path}/cronjobs/{name}/trigger")
async def trigger_cronjob(
    cluster_id: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    """Manually trigger a CronJob by creating a Job from its template."""
    if not token:
        return issue_token("trigger-cronjob", f"{name}.{namespace}",
                           {"description": f"Manually trigger CronJob {name} in {namespace}"})
    _consume(token)
    user = _user(request)
    client = await _cluster(cluster_id)
    cj = await kube_get(client, "cronjobs", name, namespace)
    job_template = cj.get("spec", {}).get("jobTemplate", {})
    job_name = f"{name}-manual-{int(time.time())}"
    job_body = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "annotations": {"cronjob.kubernetes.io/instantiate": "manual"},
        },
        "spec": job_template.get("spec", {}),
    }
    resp = await client.post(f"/apis/batch/v1/namespaces/{namespace}/jobs", json=job_body)
    if resp.status_code not in (200, 201):
        raise HTTPException(resp.status_code, resp.text[:200])
    await audit_emit(user, "trigger-cronjob", cluster_id, namespace, "CronJob", name,
                     {"job_name": job_name})
    return {"ok": True, "name": name, "job_name": job_name}


@app.post("/clusters/{cluster_id:path}/cronjobs/{name}/suspend")
async def suspend_cronjob(cluster_id: str, name: str,
                          namespace: str = Query(...), token: str = Query(""),
                          suspend: bool = Query(True)):
    """Toggle CronJob suspend flag (suspend=true pauses scheduling, suspend=false resumes)."""
    action = "suspend" if suspend else "unsuspend"
    if not token:
        return issue_token(f"{action}-cronjob", f"{namespace}/{name}", {"suspend": suspend})
    consume_token(token)
    client = await _cluster(cluster_id)
    await kube_patch(client, "cronjobs", name, namespace, {"spec": {"suspend": suspend}})
    return {"ok": True, "suspended": suspend}


@app.post("/clusters/{cluster_id:path}/nodes/{node}/cordon")
async def cordon_node(cluster_id: str, node: str, request: Request, token: str = Query("")):
    if not token:
        return issue_token("cordon", f"{cluster_id}/nodes/{node}",
                           {"description": f"Cordon node {node} in {cluster_id}"})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "cordon", cluster_id, "", "Node", node)
    client = await _cluster(cluster_id)
    await kube_patch(client, "nodes", node, None, {"spec": {"unschedulable": True}})
    return {"ok": True}


@app.post("/clusters/{cluster_id:path}/nodes/{node}/uncordon")
async def uncordon_node(cluster_id: str, node: str, request: Request):
    user = _user(request)
    await audit_emit(user, "uncordon", cluster_id, "", "Node", node)
    client = await _cluster(cluster_id)
    await kube_patch(client, "nodes", node, None, {"spec": {"unschedulable": False}})
    return {"ok": True}


@app.post("/clusters/{cluster_id:path}/nodes/{node}/drain")
async def drain_node(cluster_id: str, node: str, request: Request, token: str = Query("")):
    if not token:
        return issue_token("drain", f"{cluster_id}/nodes/{node}",
                           {"description": f"Drain node {node} in {cluster_id} (evicts all pods)"})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "drain", cluster_id, "", "Node", node)
    # Cordon first, then evict all pods
    client = await _cluster(cluster_id)
    await kube_patch(client, "nodes", node, None, {"spec": {"unschedulable": True}})
    pods = await kube_list(client, "pods", field_selector=f"spec.nodeName={node}")
    evicted = 0
    for pod in pods:
        meta = pod.get("metadata", {})
        pod_ns = meta.get("namespace", "")
        pod_name = meta.get("name", "")
        owners = meta.get("ownerReferences", [])
        # Skip DaemonSet pods
        if any(o.get("kind") == "DaemonSet" for o in owners):
            continue
        try:
            await client.post(
                f"/api/v1/namespaces/{pod_ns}/pods/{pod_name}/eviction",
                json={"apiVersion": "policy/v1", "kind": "Eviction",
                      "metadata": {"name": pod_name, "namespace": pod_ns}},
            )
            evicted += 1
        except Exception:
            pass
    return {"ok": True, "evicted": evicted}


@app.post("/clusters/{cluster_id:path}/nodes/{node}/labels")
async def edit_node_labels(cluster_id: str, node: str, request: Request, token: str = Query("")):
    """Add or remove labels on a node (strategic merge patch)."""
    body = await request.json()
    add: dict = body.get("add", {})
    remove: list = body.get("remove", [])
    description = f"Edit labels on node {node}: +{list(add.keys())} -{remove}"
    if not token:
        return issue_token("edit-node-labels", node,
                           {"description": description, "add": add, "remove": remove})
    _consume(token)
    user = _user(request)
    client = await _cluster(cluster_id)
    patch_labels = {**add, **{k: None for k in remove}}
    resp = await client.patch(
        f"/api/v1/nodes/{node}",
        json={"metadata": {"labels": patch_labels}},
        headers={"Content-Type": "application/strategic-merge-patch+json"},
    )
    if resp.status_code not in (200, 201):
        raise HTTPException(resp.status_code, resp.text[:200])
    await audit_emit(user, "edit-node-labels", cluster_id, "", "Node", node, {"add": add, "remove": remove})
    return {"ok": True}


@app.post("/clusters/{cluster_id:path}/nodes/{node}/taints")
async def edit_node_taints(cluster_id: str, node: str, request: Request, token: str = Query("")):
    """Add or remove a taint on a node."""
    body = await request.json()
    action: str = body.get("action", "")
    taint: dict = body.get("taint", {})
    if action not in ("add", "remove"):
        raise HTTPException(400, "action must be 'add' or 'remove'")
    description = f"{action.capitalize()} taint {taint.get('key')}:{taint.get('effect')} on node {node}"
    if not token:
        return issue_token("edit-node-taints", node,
                           {"description": description, "action": action, "taint": taint})
    _consume(token)
    user = _user(request)
    client = await _cluster(cluster_id)
    node_resp = await client.get(f"/api/v1/nodes/{node}")
    current_taints = (node_resp.json().get("spec") or {}).get("taints") or []
    if action == "add":
        filtered = [t for t in current_taints
                    if not (t.get("key") == taint.get("key") and t.get("effect") == taint.get("effect"))]
        new_taints = filtered + [taint]
    else:
        new_taints = [t for t in current_taints
                      if not (t.get("key") == taint.get("key") and t.get("effect") == taint.get("effect"))]
    resp = await client.patch(
        f"/api/v1/nodes/{node}",
        json={"spec": {"taints": new_taints}},
        headers={"Content-Type": "application/strategic-merge-patch+json"},
    )
    if resp.status_code not in (200, 201):
        raise HTTPException(resp.status_code, resp.text[:200])
    await audit_emit(user, "edit-node-taints", cluster_id, "", "Node", node, {"action": action, "taint": taint})
    return {"ok": True, "taints": new_taints}


# ── Phase B: Secrets reveal ───────────────────────────────────────────────────

@app.post("/clusters/{cluster_id:path}/secrets/{name}/reveal")
async def reveal_secret(
    cluster_id: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    if not token:
        return issue_token("reveal-secret", f"{cluster_id}/{namespace}/secrets/{name}",
                           {"description": f"Reveal secret values for {name} in {namespace}/{cluster_id}"})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "reveal-secret", cluster_id, namespace, "Secret", name)
    client = await _cluster(cluster_id)
    url = f"/api/v1/namespaces/{namespace}/secrets/{name}"
    resp = await client.get(url)
    resp.raise_for_status()
    obj = resp.json()
    # Decode base64 values
    decoded = {}
    for k, v in (obj.get("data") or {}).items():
        try:
            import base64
            decoded[k] = base64.b64decode(v).decode("utf-8", errors="replace")
        except Exception:
            decoded[k] = v
    return {"name": name, "namespace": namespace, "data": decoded}


@app.delete("/clusters/{cluster_id:path}/secrets/{name}")
async def delete_secret(
    cluster_id: str,
    name: str,
    request: Request,
    namespace: str = Query(...),
    token: str = Query(""),
):
    if not token:
        return issue_token("delete-secret", f"{cluster_id}/{namespace}/secrets/{name}",
                           {"description": f"Delete secret {name} in namespace {namespace} — irreversible"})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "delete-secret", cluster_id, namespace, "Secret", name)
    client = await _cluster(cluster_id)
    await kube_delete(client, "secrets", name, namespace)
    return {"ok": True, "name": name, "namespace": namespace}


# ── Resource YAML viewer ─────────────────────────────────────────────────────

@app.get("/clusters/{cluster_id:path}/raw/{kind}/{name}")
async def get_resource_yaml(cluster_id: str, kind: str, name: str, namespace: str = ""):
    """Return a resource as sanitized YAML for display."""
    client = await _cluster(cluster_id)
    obj = await kube_get(client, kind, name, namespace or None)
    # Strip managed fields to keep the YAML readable
    obj.get("metadata", {}).pop("managedFields", None)
    return {"yaml": _yaml.dump(obj, default_flow_style=False, allow_unicode=True)}


@app.put("/clusters/{cluster_id:path}/raw/{kind}/{name}")
async def patch_resource_yaml(cluster_id: str, kind: str, name: str, request: Request, namespace: str = ""):
    """Apply edited YAML back to the cluster (strategic merge via kube_apply)."""
    body = await request.json()
    yaml_text = body.get("yaml", "")
    if not yaml_text:
        raise HTTPException(400, "yaml required")
    user = _user(request)
    client = await _cluster(cluster_id)
    doc = _yaml.safe_load(yaml_text)
    if not isinstance(doc, dict):
        raise HTTPException(422, "YAML must be a mapping")
    result = await kube_apply(client, doc)
    await audit_emit("yaml_edit", user, cluster_id, {
        "kind": kind, "name": name, "namespace": namespace,
    })
    return {"status": "applied", "name": result.get("metadata", {}).get("name")}


# ── Phase C: Apply / Create ───────────────────────────────────────────────────

@app.post("/clusters/{cluster_id:path}/apply")
async def apply_yaml(cluster_id: str, request: Request):
    """Apply one or more YAML documents (import YAML)."""
    body = await request.json()
    yaml_text = body.get("yaml", "")
    if not yaml_text:
        raise HTTPException(400, "yaml required")

    user = _user(request)
    client = await _cluster(cluster_id)

    docs = list(_yaml.safe_load_all(yaml_text))
    results = []
    for doc in docs:
        if not doc:
            continue
        kind = doc.get("kind", "")
        name = doc.get("metadata", {}).get("name", "")
        ns = doc.get("metadata", {}).get("namespace", "")
        await audit_emit(user, "apply", cluster_id, ns, kind, name)
        try:
            result = await kube_apply(client, doc)
            results.append({"kind": kind, "name": name, "ok": True})
        except Exception as e:
            results.append({"kind": kind, "name": name, "ok": False, "error": str(e)})
    return {"results": results}


_QUOTA_PRESETS = {
    "small":  {"requests.cpu": "4",    "requests.memory": "8Gi",   "limits.cpu": "8",    "limits.memory": "16Gi",  "pods": "20"},
    "medium": {"requests.cpu": "16",   "requests.memory": "32Gi",  "limits.cpu": "32",   "limits.memory": "64Gi",  "pods": "50"},
    "large":  {"requests.cpu": "64",   "requests.memory": "128Gi", "limits.cpu": "128",  "limits.memory": "256Gi", "pods": "200"},
}

_LIMIT_PRESETS = {
    "small":  {"cpu_req": "100m", "cpu_lim": "500m", "mem_req": "128Mi", "mem_lim": "512Mi"},
    "medium": {"cpu_req": "250m", "cpu_lim": "2",    "mem_req": "256Mi", "mem_lim": "2Gi"},
    "large":  {"cpu_req": "500m", "cpu_lim": "4",    "mem_req": "512Mi", "mem_lim": "8Gi"},
}


@app.post("/clusters/{cluster_id:path}/namespaces")
async def create_namespace(cluster_id: str, request: Request, token: str = Query("")):
    body = await request.json()
    name = body.get("name")
    if not name:
        raise HTTPException(400, "name required")
    quota_preset = body.get("quota_preset", "")   # small|medium|large|custom|""
    quota_custom: dict = body.get("quota_custom", {})
    limits_preset = body.get("limits_preset", "")  # small|medium|large|""
    labels: dict = body.get("labels", {})
    if token:
        try:
            consume_token(token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        user = _user(request)
        await audit_emit(user, "create", cluster_id, name, "Namespace", name)
        client = await _cluster(cluster_id)
        ns_manifest = {"apiVersion": "v1", "kind": "Namespace",
                       "metadata": {"name": name, "labels": labels}}
        await kube_apply(client, ns_manifest)
        # Optional ResourceQuota
        hard = dict(_QUOTA_PRESETS.get(quota_preset, {}))
        hard.update(quota_custom)
        if hard:
            quota_manifest = {
                "apiVersion": "v1", "kind": "ResourceQuota",
                "metadata": {"name": f"{name}-quota", "namespace": name},
                "spec": {"hard": hard},
            }
            await kube_apply(client, quota_manifest)
        # Optional LimitRange
        lp = _LIMIT_PRESETS.get(limits_preset, {})
        if lp:
            lr_manifest = {
                "apiVersion": "v1", "kind": "LimitRange",
                "metadata": {"name": f"{name}-limits", "namespace": name},
                "spec": {"limits": [{"type": "Container", "default": {"cpu": lp["cpu_lim"], "memory": lp["mem_lim"]},
                                     "defaultRequest": {"cpu": lp["cpu_req"], "memory": lp["mem_req"]}}]},
            }
            await kube_apply(client, lr_manifest)
        return {"ok": True, "name": name}
    return issue_token("create-namespace", f"{cluster_id}/namespaces/{name}", {
        "quota_preset": quota_preset, "limits_preset": limits_preset,
    })


@app.delete("/clusters/{cluster_id:path}/namespaces/{name}")
async def delete_namespace(cluster_id: str, name: str, request: Request, token: str = Query("")):
    if not token:
        return issue_token("delete-namespace", f"{cluster_id}/namespaces/{name}",
                           {"description": f"Delete namespace {name} in {cluster_id} — removes ALL resources in it"})
    _consume(token)
    user = _user(request)
    await audit_emit(user, "delete", cluster_id, name, "Namespace", name)
    client = await _cluster(cluster_id)
    url = f"/api/v1/namespaces/{name}"
    resp = await client.delete(url)
    resp.raise_for_status()
    return {"ok": True}


@app.patch("/clusters/{cluster_id:path}/namespaces/{name}/labels")
async def patch_namespace_labels(cluster_id: str, name: str, body: dict = Body(...),
                                 token: str = Query("")):
    """Merge-patch namespace labels. Keys with null value are removed."""
    labels: dict = body.get("labels", {})
    if not labels:
        raise HTTPException(400, "labels dict is required")
    if not token:
        return issue_token("patch-ns-labels", name, {"labels": labels})
    consume_token(token)
    client = await _cluster(cluster_id)
    await kube_patch(client, "namespaces", name, None, {"metadata": {"labels": labels}})
    return {"ok": True, "name": name}


@app.post("/clusters/{cluster_id:path}/workloads")
async def create_workload(cluster_id: str, request: Request):
    """Create a workload from a structured form payload or raw YAML."""
    body = await request.json()
    manifest = body.get("manifest")
    if not manifest:
        raise HTTPException(400, "manifest required")
    user = _user(request)
    kind = manifest.get("kind", "")
    name = manifest.get("metadata", {}).get("name", "")
    ns = manifest.get("metadata", {}).get("namespace", "default")
    await audit_emit(user, "create", cluster_id, ns, kind, name)
    client = await _cluster(cluster_id)
    result = await kube_apply(client, manifest)
    return {"ok": True, "result": result}


@app.post("/clusters/{cluster_id:path}/configmaps")
async def create_configmap(cluster_id: str, request: Request):
    body = await request.json()
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": body["name"], "namespace": body["namespace"]},
        "data": body.get("data", {}),
    }
    user = _user(request)
    await audit_emit(user, "create", cluster_id, body["namespace"], "ConfigMap", body["name"])
    client = await _cluster(cluster_id)
    result = await kube_apply(client, manifest)
    return {"ok": True, "result": result}


# ── Phase D: AI layer ─────────────────────────────────────────────────────────

@app.post("/generate/manifest")
async def generate_manifest(request: Request):
    """NL → Kubernetes manifest. Streams SSE tokens."""
    body = await request.json()
    prompt = body.get("prompt", "")
    context = body.get("context", {})  # {namespace, cluster}
    if not prompt:
        raise HTTPException(400, "prompt required")

    system_prompt = """\
You are a Kubernetes expert. Convert the user's natural language request into a valid Kubernetes manifest (YAML).
Return ONLY the YAML manifest, no explanation, no markdown fences. The manifest should be production-ready with:
- appropriate resource requests and limits
- readiness/liveness probes where applicable
- proper labels (app: name)
If multiple manifests are needed (e.g. Deployment + Service), output them separated by ---.
"""
    if context.get("namespace"):
        system_prompt += f"\nDefault namespace: {context['namespace']}"

    async def _stream():
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{LLM_GATEWAY_URL}/generate/kubectl",
                    json={"query": f"Generate a Kubernetes manifest for: {prompt}", "system_override": system_prompt},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    manifest_text = data.get("generated", "") or data.get("command", "")
                    yield f"data: {json.dumps({'manifest': manifest_text, 'done': True})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': 'LLM unavailable'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/clusters/{cluster_id:path}/pods/{pod}/diagnose")
async def diagnose_pod(
    cluster_id: str,
    pod: str,
    namespace: str = Query(...),
    mode: str = Query("diagnose"),
):
    """AI pod diagnosis (mode=diagnose) or K8s concept explanation (mode=teach)."""
    client = await _cluster(cluster_id)

    pod_info, pod_events_raw = await asyncio.gather(
        kube_get(client, "pods", pod, namespace),
        kube_list(client, "events", namespace, field_selector=f"involvedObject.name={pod}"),
    )
    log_resp = await client.get(
        f"/api/v1/namespaces/{namespace}/pods/{pod}/log",
        params={"tailLines": 100, "previous": "false"},
    )
    logs = log_resp.text if log_resp.status_code == 200 else "(no logs)"

    container_statuses = pod_info.get("status", {}).get("containerStatuses", [])
    phase = pod_info.get("status", {}).get("phase", "Unknown")
    conditions = pod_info.get("status", {}).get("conditions", [])
    events_text = "\n".join(
        f"[{e['type']}] {e['reason']}: {e['message']}"
        for e in [_format_event(ev) for ev in pod_events_raw[:20]]
    )

    if mode == "teach":
        prompt = f"""Pod: {pod} in namespace {namespace}
Phase: {phase}
Container statuses: {json.dumps(container_statuses, indent=2)}
Recent events:
{events_text}

You are explaining this to an ICT admin who is new to Kubernetes and moving from a different platform.
Please explain:
1. **What is happening** — describe this pod's current state in plain language (no jargon)
2. **The Kubernetes concept** — what K8s feature or mechanism caused this (e.g. resource limits, liveness probes, OOM killer, image pull policy)
3. **Why Kubernetes does this** — the design reason behind this behavior
4. **What to learn next** — one specific K8s concept to study that will help them understand this better
5. **Hands-on exercise** — one simple `kubectl` command or action they can run right now to build understanding

Use markdown formatting with headers and code blocks."""
    else:
        prompt = f"""Pod: {pod} in {namespace}/{cluster_id}
Phase: {phase}
Container statuses: {json.dumps(container_statuses, indent=2)}
Conditions: {json.dumps(conditions, indent=2)}
Recent events:
{events_text}
Last 100 log lines:
{logs[:3000]}

Diagnose the problem and provide (use markdown):
## Root Cause
(1-2 sentences)

## How to Fix
(concrete kubectl commands or config changes)

## How to Prevent
(best practice recommendation)"""

    async def _stream():
        try:
            async with httpx.AsyncClient(timeout=90.0) as http:
                resp = await http.post(
                    f"{LLM_GATEWAY_URL}/explain",
                    json={
                        "scoring_result": {"readiness_score": 0, "status": "DIAGNOSING", "risk_factors": [], "recommendations": []},
                        "raw_data": {"pod": pod_info, "logs": logs, "events": events_text},
                        "query": prompt,
                        "target": "k8s_pod_diagnosis",
                    },
                )
                if resp.status_code == 200:
                    ct = resp.headers.get("content-type", "")
                    if "event-stream" in ct:
                        for line in resp.text.splitlines():
                            if line.startswith("data:"):
                                yield f"{line}\n\n"
                    else:
                        data = resp.json()
                        explanation = data.get("explanation") or data.get("text", "")
                        yield f"data: {json.dumps({'text': explanation, 'done': True})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': 'LLM unavailable'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/nl/action")
async def nl_action(request: Request):
    """NL command bar: 'scale frontend to 5 in production' → typed action + confirm diff."""
    body = await request.json()
    query = body.get("query", "")
    cluster_id = body.get("cluster_id", "")
    if not query:
        raise HTTPException(400, "query required")

    nl_prompt = f"""Convert this natural language cluster management request into a structured action JSON.
Request: "{query}"
Available actions: scale, restart, delete-pod, delete-workload, cordon, uncordon, drain

Return ONLY this JSON (no markdown):
{{"action": "scale", "kind": "Deployment", "name": "frontend", "namespace": "production", "params": {{"replicas": 5}}, "description": "human readable description"}}

If the request is ambiguous, set "action": "unknown" and explain in "description".
"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                f"{LLM_GATEWAY_URL}/generate/kubectl",
                json={"query": nl_prompt},
            )
            resp.raise_for_status()
            data = resp.json()
            generated = data.get("generated") or data.get("command", "{}")
            # Extract JSON from response
            m = re.search(r'\{.*\}', generated, re.DOTALL)
            if m:
                action_data = json.loads(m.group())
            else:
                action_data = {"action": "unknown", "description": generated}
    except Exception as e:
        action_data = {"action": "unknown", "description": str(e)}

    return {"query": query, "parsed": action_data}



# ── CRD Browser ───────────────────────────────────────────────────────────────


# ── Cluster Summary ───────────────────────────────────────────────────────────

@app.get("/clusters/{cluster_id:path}/summary")
async def cluster_summary(cluster_id: str):
    """Compact cluster summary for comparison: nodes, pods, workloads, version."""
    client = await _cluster(cluster_id)
    version_r, nodes_r, pods_r, deps_r, sts_r, nss_r = await asyncio.gather(
        client.get("/version"),
        kube_list(client, "nodes"),
        kube_list(client, "pods"),
        kube_list(client, "deployments"),
        kube_list(client, "statefulsets"),
        kube_list(client, "namespaces"),
        return_exceptions=True,
    )

    def _safe(v):
        return v if not isinstance(v, Exception) else []

    nodes = _safe(nodes_r)
    pods = _safe(pods_r)
    deps = _safe(deps_r)
    sts = _safe(sts_r)
    namespaces = _safe(nss_r)

    version = ""
    if not isinstance(version_r, Exception):
        try:
            vj = version_r.json()
            version = f"{vj.get('major', '')}.{vj.get('minor', '')}"
        except Exception:
            pass

    nodes_ready = sum(
        1 for n in nodes
        if any(c.get("type") == "Ready" and c.get("status") == "True"
               for c in n.get("status", {}).get("conditions", []))
    )

    pod_phases: dict = {}
    for p in pods:
        phase = p.get("status", {}).get("phase", "Unknown")
        pod_phases[phase] = pod_phases.get(phase, 0) + 1

    workloads_total = len(deps) + len(sts)
    workloads_healthy = 0
    for w in [*deps, *sts]:
        spec = w.get("spec", {})
        status = w.get("status", {})
        desired = spec.get("replicas") or 1
        ready = status.get("readyReplicas") or 0
        if ready >= desired:
            workloads_healthy += 1

    total_cpu_m = 0.0
    total_mem_mib = 0
    for n in nodes:
        alloc = n.get("status", {}).get("allocatable", {})
        total_cpu_m += _cpu_to_m(alloc.get("cpu", "0"))
        total_mem_mib += _mem_to_mib(alloc.get("memory", "0"))

    return {
        "cluster_id": cluster_id,
        "version": version,
        "nodes": {"total": len(nodes), "ready": nodes_ready},
        "pods": {**pod_phases, "total": len(pods)},
        "workloads": {"total": workloads_total, "healthy": workloads_healthy},
        "namespaces": len(namespaces),
        "capacity": {
            "cpu_cores": round(total_cpu_m / 1000, 1),
            "memory_gib": round(total_mem_mib / 1024, 1),
        },
    }


# ── Namespace Clone ───────────────────────────────────────────────────────────

@app.post("/clusters/{cluster_id:path}/namespaces/{src_ns}/clone")
async def clone_namespace(cluster_id: str, src_ns: str, request: Request, token: str = Query("")):
    """Clone ConfigMaps and Secrets from src_ns into a new namespace."""
    body = await request.json()
    target_ns: str = body.get("target_namespace", "").strip()
    resource_types: list = body.get("resource_types", ["configmaps"])
    if not target_ns:
        raise HTTPException(400, "target_namespace required")
    if not all(r in ("configmaps", "secrets") for r in resource_types):
        raise HTTPException(400, "resource_types may only include configmaps, secrets")

    description = f"Clone {', '.join(resource_types)} from {src_ns} to {target_ns}"
    if not token:
        return issue_token("namespace-clone", src_ns,
                           {"description": description, "target_namespace": target_ns, "resource_types": resource_types})
    _consume(token)
    user = _user(request)
    client = await _cluster(cluster_id)

    ns_check = await client.get(f"/api/v1/namespaces/{target_ns}")
    if ns_check.status_code == 404:
        create_ns = await client.post("/api/v1/namespaces", json={
            "apiVersion": "v1", "kind": "Namespace",
            "metadata": {"name": target_ns},
        })
        if create_ns.status_code not in (200, 201):
            raise HTTPException(create_ns.status_code, f"Failed to create namespace: {create_ns.text[:200]}")

    cloned = []
    errors = []
    for rtype in resource_types:
        items = await kube_list(client, rtype, src_ns)
        for obj in items:
            meta = obj.get("metadata", {})
            name = meta.get("name", "")
            if not name or name.startswith("sh.helm.release"):
                continue
            new_obj = {
                "apiVersion": "v1",
                "kind": "ConfigMap" if rtype == "configmaps" else "Secret",
                "metadata": {"name": name, "namespace": target_ns,
                             "labels": {**meta.get("labels", {}), "cloned-from": src_ns}},
                "data": obj.get("data") or {},
            }
            if rtype == "secrets":
                new_obj["type"] = obj.get("type", "Opaque")
            resp = await client.post(f"/api/v1/namespaces/{target_ns}/{rtype}", json=new_obj)
            if resp.status_code in (200, 201):
                cloned.append(f"{rtype}/{name}")
            elif resp.status_code == 409:
                cloned.append(f"{rtype}/{name} (already existed)")
            else:
                errors.append(f"{rtype}/{name}: {resp.status_code}")

    await audit_emit(user, "namespace-clone", cluster_id, src_ns, "Namespace", target_ns,
                     {"cloned": cloned, "errors": errors})
    return {"ok": True, "target_namespace": target_ns, "cloned": cloned, "errors": errors}




# ── Workload Annotations ──────────────────────────────────────────────────────

@app.post("/clusters/{cluster_id:path}/workloads/{kind}/{name}/annotations")
async def edit_workload_annotations(cluster_id: str, kind: str, name: str, request: Request, token: str = Query("")):
    """Add or remove annotations on a Deployment or StatefulSet.
    
    Body: {"namespace": str, "add": {"key": "val"}, "remove": ["key"]}
    """
    body = await request.json()
    namespace: str = body.get("namespace", "default")
    add: dict = body.get("add", {})
    remove: list = body.get("remove", [])
    if kind not in ("deployment", "statefulset", "daemonset"):
        raise HTTPException(400, "kind must be deployment, statefulset, or daemonset")

    description = f"Edit annotations on {kind}/{name}: +{list(add.keys())} -{remove}"
    if not token:
        return issue_token("edit-annotations", name,
                           {"description": description, "namespace": namespace, "add": add, "remove": remove})
    _consume(token)
    user = _user(request)
    client = await _cluster(cluster_id)

    kind_map = {
        "deployment": ("apps", "v1", "deployments"),
        "statefulset": ("apps", "v1", "statefulsets"),
        "daemonset": ("apps", "v1", "daemonsets"),
    }
    group, version, plural = kind_map[kind]
    patch_annotations = {**add, **{k: None for k in remove}}
    resp = await client.patch(
        f"/apis/{group}/{version}/namespaces/{namespace}/{plural}/{name}",
        json={"metadata": {"annotations": patch_annotations}},
        headers={"Content-Type": "application/strategic-merge-patch+json"},
    )
    if resp.status_code not in (200, 201):
        raise HTTPException(resp.status_code, resp.text[:200])
    result_annotations = resp.json().get("metadata", {}).get("annotations", {})
    await audit_emit(user, "edit-annotations", cluster_id, namespace, kind.capitalize(), name,
                     {"add": add, "remove": remove})
    return {"ok": True, "annotations": result_annotations}


# ── Event Stream (SSE watch) ──────────────────────────────────────────────────

@app.get("/clusters/{cluster_id:path}/events/stream")
async def stream_events(cluster_id: str, namespace: str = "", reason: str = "", kind: str = ""):
    """Watch Kubernetes events via SSE. Reconnects automatically on timeout."""
    import asyncio as _asyncio

    async def _generate():
        client = await _cluster(cluster_id)
        url = "/api/v1/events"
        if namespace:
            url = f"/api/v1/namespaces/{namespace}/events"
        params: dict = {"watch": "true", "timeoutSeconds": "60"}
        if reason:
            params["fieldSelector"] = f"reason={reason}"

        try:
            async with client.stream("GET", url, params=params, timeout=70) as resp:
                if resp.status_code not in (200, 201):
                    err_code = resp.status_code
                    yield "data: " + json.dumps({"error": "Watch failed: " + str(err_code)}) + "\n\n"
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                        ev_obj = obj.get("object", {})
                        meta = ev_obj.get("metadata", {})
                        involved = ev_obj.get("involvedObject", {})
                        ev_kind = involved.get("kind", "")
                        if kind and ev_kind.lower() != kind.lower():
                            continue
                        event_data = {
                            "event_type": obj.get("type", ""),
                            "name": meta.get("name", ""),
                            "namespace": meta.get("namespace", ""),
                            "reason": ev_obj.get("reason", ""),
                            "message": ev_obj.get("message", ""),
                            "type": ev_obj.get("type", ""),
                            "involved_kind": ev_kind,
                            "involved_name": involved.get("name", ""),
                            "count": ev_obj.get("count", 1),
                            "last_time": ev_obj.get("lastTimestamp", ""),
                        }
                        yield "data: " + json.dumps(event_data) + "\n\n"
                    except Exception:
                        continue
        except Exception as e:
            yield "data: " + json.dumps({"error": str(e), "done": True}) + "\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Helm Release Browser (Loop 42) ───────────────────────────────────────────

import base64 as _base64
import gzip as _gzip


def _decode_helm_release(b64_data: str) -> dict:
    """Decode base64(gzip(json)) Helm v3 release secret."""
    try:
        compressed = _base64.b64decode(b64_data)
        raw = _gzip.decompress(compressed)
        return json.loads(raw)
    except Exception:
        return {}


def _helm_release_summary(rel: dict, namespace: str) -> dict:
    info = rel.get("info", {})
    chart_meta = rel.get("chart", {}).get("metadata", {})
    return {
        "name": rel.get("name", ""),
        "namespace": namespace,
        "revision": rel.get("version", 0),
        "status": info.get("status", "unknown"),
        "chart_name": chart_meta.get("name", ""),
        "chart_version": chart_meta.get("version", ""),
        "app_version": chart_meta.get("appVersion", ""),
        "description": info.get("description", ""),
        "first_deployed": info.get("first_deployed", ""),
        "last_deployed": info.get("last_deployed", ""),
    }


_HELM_SENSITIVE_KEYS = re.compile(
    r"(password|passwd|secret|token|key|cert|credential|auth|private)", re.IGNORECASE
)


def _scrub_helm_values(values: dict, depth: int = 0) -> dict:
    if depth > 10:
        return {}
    out = {}
    for k, v in values.items():
        if _HELM_SENSITIVE_KEYS.search(k):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _scrub_helm_values(v, depth + 1)
        else:
            out[k] = v
    return out


async def _list_helm_secrets(client, namespace: str = "") -> list[dict]:
    """List Helm v3 release secrets across all (or one) namespace."""
    if namespace:
        url = f"/api/v1/namespaces/{namespace}/secrets"
    else:
        url = "/api/v1/secrets"
    resp = await client.get(url, params={"labelSelector": "owner=helm"})
    if resp.status_code not in (200, 201):
        return []
    return resp.json().get("items", [])


@app.get("/clusters/{cluster_id:path}/helm/releases")
async def list_helm_releases(cluster_id: str, namespace: str = ""):
    """List all Helm v3 releases, returning only the latest revision per release."""
    client = await _cluster(cluster_id)
    secrets = await _list_helm_secrets(client, namespace)

    # Track latest revision per (namespace, release_name)
    latest: dict[tuple, dict] = {}
    for secret in secrets:
        data = secret.get("data", {})
        raw_b64 = data.get("release", "")
        if not raw_b64:
            continue
        rel = _decode_helm_release(raw_b64)
        if not rel:
            continue
        ns = secret.get("metadata", {}).get("namespace", "")
        rel_name = rel.get("name", "")
        rev = rel.get("version", 0)
        key = (ns, rel_name)
        if key not in latest or rev > latest[key]["version"]:
            latest[key] = rel
            latest[key]["_ns"] = ns

    releases = [_helm_release_summary(r, r["_ns"]) for r in latest.values()]
    releases.sort(key=lambda r: (r["namespace"], r["name"]))
    return {"releases": releases, "total": len(releases)}


@app.get("/clusters/{cluster_id:path}/helm/releases/{namespace}/{name}/values")
async def get_helm_release_values(cluster_id: str, namespace: str, name: str):
    """Return user-supplied values for the latest revision of a Helm release."""
    client = await _cluster(cluster_id)
    secrets = await _list_helm_secrets(client, namespace)

    best_rev = -1
    best_rel = None
    for secret in secrets:
        data = secret.get("data", {})
        raw_b64 = data.get("release", "")
        if not raw_b64:
            continue
        rel = _decode_helm_release(raw_b64)
        if rel.get("name") != name:
            continue
        rev = rel.get("version", 0)
        if rev > best_rev:
            best_rev = rev
            best_rel = rel

    if not best_rel:
        raise HTTPException(404, f"Helm release '{name}' not found in namespace '{namespace}'")

    user_values = best_rel.get("config", {}) or {}
    scrubbed = _scrub_helm_values(user_values)
    values_yaml = _yaml.dump(scrubbed, default_flow_style=False, allow_unicode=True) if scrubbed else "# no user-supplied values\n"
    return {"name": name, "namespace": namespace, "revision": best_rev, "values_yaml": values_yaml}


@app.get("/clusters/{cluster_id:path}/helm/releases/{namespace}/{name}/history")
async def get_helm_release_history(cluster_id: str, namespace: str, name: str):
    """Return all revisions of a Helm release, newest first."""
    client = await _cluster(cluster_id)
    secrets = await _list_helm_secrets(client, namespace)

    history = []
    for secret in secrets:
        data = secret.get("data", {})
        raw_b64 = data.get("release", "")
        if not raw_b64:
            continue
        rel = _decode_helm_release(raw_b64)
        if rel.get("name") != name:
            continue
        info = rel.get("info", {})
        chart_meta = rel.get("chart", {}).get("metadata", {})
        history.append({
            "revision": rel.get("version", 0),
            "status": info.get("status", "unknown"),
            "chart_version": chart_meta.get("version", ""),
            "app_version": chart_meta.get("appVersion", ""),
            "description": info.get("description", ""),
            "deployed_at": info.get("last_deployed", ""),
        })

    if not history:
        raise HTTPException(404, f"Helm release '{name}' not found in namespace '{namespace}'")

    history.sort(key=lambda r: r["revision"], reverse=True)
    return {"name": name, "namespace": namespace, "history": history}


@app.get("/clusters/{cluster_id:path}/helm/releases/{namespace}/{name}/manifest")
async def get_helm_release_manifest(cluster_id: str, namespace: str, name: str):
    """Return rendered manifest from the latest revision of a Helm release."""
    client = await _cluster(cluster_id)
    secrets = await _list_helm_secrets(client, namespace)

    best_rev = -1
    best_rel = None
    for secret in secrets:
        data = secret.get("data", {})
        raw_b64 = data.get("release", "")
        if not raw_b64:
            continue
        rel = _decode_helm_release(raw_b64)
        if rel.get("name") != name:
            continue
        rev = rel.get("version", 0)
        if rev > best_rev:
            best_rev = rev
            best_rel = rel

    if not best_rel:
        raise HTTPException(404, f"Helm release '{name}' not found in namespace '{namespace}'")

    manifest = best_rel.get("manifest", "") or ""
    # Count distinct resource kinds in the manifest
    kinds = re.findall(r"^kind:\s*(\S+)", manifest, re.MULTILINE)
    return {
        "name": name,
        "namespace": namespace,
        "revision": best_rev,
        "manifest": manifest,
        "resource_count": len(kinds),
        "resource_kinds": sorted(set(kinds)),
    }


# ── Cross-Namespace Resource Search (Loop 43) ────────────────────────────────

_SEARCH_KIND_MAP: dict[str, tuple[str, str]] = {
    # kind_key: (list_url_template, display_kind)
    "pods":            ("/api/v1/pods",                     "Pod"),
    "deployments":     ("/apis/apps/v1/deployments",        "Deployment"),
    "statefulsets":    ("/apis/apps/v1/statefulsets",       "StatefulSet"),
    "daemonsets":      ("/apis/apps/v1/daemonsets",         "DaemonSet"),
    "services":        ("/api/v1/services",                 "Service"),
    "configmaps":      ("/api/v1/configmaps",               "ConfigMap"),
    "secrets":         ("/api/v1/secrets",                  "Secret"),
    "ingresses":       ("/apis/networking.k8s.io/v1/ingresses", "Ingress"),
    "jobs":            ("/apis/batch/v1/jobs",              "Job"),
    "cronjobs":        ("/apis/batch/v1/cronjobs",          "CronJob"),
    "pvcs":            ("/api/v1/persistentvolumeclaims",   "PVC"),
    "serviceaccounts": ("/api/v1/serviceaccounts",          "ServiceAccount"),
}

_DEFAULT_SEARCH_KINDS = {"pods", "deployments", "statefulsets", "services", "configmaps", "ingresses"}


def _search_match(item: dict, q: str) -> bool:
    meta = item.get("metadata", {})
    name = meta.get("name", "").lower()
    ns = meta.get("namespace", "").lower()
    labels = " ".join(f"{k}={v}" for k, v in (meta.get("labels") or {}).items()).lower()
    annotations_vals = " ".join((meta.get("annotations") or {}).values()).lower()
    return q in name or q in ns or q in labels or q in annotations_vals


def _search_item_summary(item: dict, kind_display: str) -> dict:
    meta = item.get("metadata", {})
    labels = meta.get("labels") or {}
    status_obj = item.get("status", {})
    # Derive a brief status string per kind
    phase = status_obj.get("phase", "")
    ready_replicas = status_obj.get("readyReplicas")
    replicas = item.get("spec", {}).get("replicas")
    if phase:
        brief_status = phase
    elif ready_replicas is not None and replicas is not None:
        brief_status = f"{ready_replicas}/{replicas} ready"
    else:
        conditions = status_obj.get("conditions", [])
        ready_cond = next((c for c in conditions if c.get("type") == "Ready"), None)
        brief_status = ready_cond.get("status", "") if ready_cond else ""
    return {
        "kind": kind_display,
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "labels": dict(list(labels.items())[:6]),
        "created_at": meta.get("creationTimestamp", ""),
        "status": brief_status,
    }


@app.get("/clusters/{cluster_id:path}/search")
async def cross_namespace_search(
    cluster_id: str,
    q: str = "",
    kinds: str = "",
    namespace: str = "",
    limit: int = 100,
):
    """Search resources by name, namespace, or label across all (or specified) kinds."""
    if not q or len(q) < 2:
        raise HTTPException(400, "Query 'q' must be at least 2 characters")
    if limit > 500:
        limit = 500

    q_lower = q.lower()
    client = await _cluster(cluster_id)

    kind_set: set[str]
    if kinds:
        kind_set = {k.strip().lower() for k in kinds.split(",") if k.strip()}
        kind_set = kind_set & set(_SEARCH_KIND_MAP.keys())
    else:
        kind_set = _DEFAULT_SEARCH_KINDS

    tasks = []
    for kind_key in kind_set:
        url_template, display = _SEARCH_KIND_MAP[kind_key]
        if namespace:
            # Insert namespace into path for namespaced resources
            if url_template.startswith("/api/v1/"):
                resource = url_template.split("/")[-1]
                url = f"/api/v1/namespaces/{namespace}/{resource}"
            elif "/v1/" in url_template:
                parts = url_template.split("/v1/")
                url = parts[0] + f"/v1/namespaces/{namespace}/" + parts[1].split("/")[-1]
            else:
                url = url_template
        else:
            url = url_template
        tasks.append((kind_key, display, url))

    results = []
    fetch_coros = [client.get(url) for (_, _, url) in tasks]
    responses = await asyncio.gather(*fetch_coros, return_exceptions=True)

    for (kind_key, display, _), resp in zip(tasks, responses):
        if isinstance(resp, Exception):
            continue
        if getattr(resp, "status_code", 999) not in (200, 201):
            continue
        items = resp.json().get("items", [])
        for item in items:
            if _search_match(item, q_lower):
                results.append(_search_item_summary(item, display))
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    results.sort(key=lambda r: (r["namespace"], r["kind"], r["name"]))
    return {"query": q, "results": results, "total": len(results), "kinds_searched": sorted(kind_set)}


@app.get("/clusters/{cluster_id:path}/crds")
async def list_crds(cluster_id: str):
    """List all Custom Resource Definitions in the cluster."""
    client = await _cluster(cluster_id)
    resp = await client.get("/apis/apiextensions.k8s.io/v1/customresourcedefinitions")
    if resp.status_code not in (200, 201):
        raise HTTPException(resp.status_code, resp.text[:200])
    crds = resp.json().get("items", [])
    result = []
    for crd in crds:
        meta = crd.get("metadata", {})
        spec = crd.get("spec", {})
        status = crd.get("status", {})
        versions = [v.get("name", "") for v in spec.get("versions", []) if v.get("served")]
        conditions = [
            {"type": c.get("type", ""), "status": c.get("status", ""), "reason": c.get("reason", "")}
            for c in status.get("conditions", [])
        ]
        established = next((c["status"] for c in conditions if c["type"] == "Established"), "Unknown")
        result.append({
            "name": meta.get("name", ""),
            "group": spec.get("group", ""),
            "scope": spec.get("scope", ""),
            "kind": spec.get("names", {}).get("kind", ""),
            "plural": spec.get("names", {}).get("plural", ""),
            "versions": versions,
            "established": established,
            "created_at": meta.get("creationTimestamp", ""),
        })
    result.sort(key=lambda x: x["name"])
    return {"crds": result, "total": len(result)}


@app.get("/clusters/{cluster_id:path}/crds/{crd_name}/instances")
async def list_crd_instances(cluster_id: str, crd_name: str, namespace: str = ""):
    """List instances of a specific CRD."""
    client = await _cluster(cluster_id)
    # Fetch CRD metadata to get group/version/plural/scope
    crd_resp = await client.get(f"/apis/apiextensions.k8s.io/v1/customresourcedefinitions/{crd_name}")
    if crd_resp.status_code == 404:
        raise HTTPException(404, f"CRD {crd_name} not found")
    if crd_resp.status_code not in (200, 201):
        raise HTTPException(crd_resp.status_code, crd_resp.text[:200])
    crd = crd_resp.json()
    spec = crd.get("spec", {})
    group = spec.get("group", "")
    scope = spec.get("scope", "")
    plural = spec.get("names", {}).get("plural", "")
    # Use first served version
    version = next((v["name"] for v in spec.get("versions", []) if v.get("served")), "v1")

    if scope == "Namespaced" and namespace:
        url = f"/apis/{group}/{version}/namespaces/{namespace}/{plural}"
    elif scope == "Namespaced" and not namespace:
        url = f"/apis/{group}/{version}/{plural}"
    else:
        url = f"/apis/{group}/{version}/{plural}"

    resp = await client.get(url)
    if resp.status_code not in (200, 201):
        raise HTTPException(resp.status_code, resp.text[:200])
    items = resp.json().get("items", [])
    result = []
    for obj in items:
        meta = obj.get("metadata", {})
        result.append({
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "created_at": meta.get("creationTimestamp", ""),
            "labels": meta.get("labels", {}),
        })
    return {"instances": result, "total": len(result), "crd": crd_name}


# ── Resource YAML Viewer ──────────────────────────────────────────────────────

@app.get("/clusters/{cluster_id:path}/resource-yaml")
async def get_resource_yaml(
    cluster_id: str,
    api_path: str,
):
    """Fetch raw YAML/JSON for any resource given its API path.
    
    api_path: e.g. /api/v1/namespaces/default/pods/my-pod
    """
    client = await _cluster(cluster_id)
    if not api_path.startswith("/api"):
        raise HTTPException(400, "api_path must start with /api or /apis")
    resp = await client.get(api_path, headers={"Accept": "application/json"})
    if resp.status_code == 404:
        raise HTTPException(404, "Resource not found")
    if resp.status_code not in (200, 201):
        raise HTTPException(resp.status_code, resp.text[:200])
    obj = resp.json()
    # Scrub secrets
    kind = obj.get("kind", "")
    if kind == "Secret":
        obj["data"] = {k: "***" for k in obj.get("data", {})}
        obj.get("stringData", {}).clear()
    import yaml as _yaml
    try:
        yaml_str = _yaml.dump(obj, default_flow_style=False, allow_unicode=True)
    except Exception:
        yaml_str = json.dumps(obj, indent=2)
    return {"yaml": yaml_str, "kind": kind, "name": obj.get("metadata", {}).get("name", "")}


# ── Formatters ────────────────────────────────────────────────────────────────

def _cpu_to_m(cpu: str) -> float:
    """Convert CPU string (1, 500m, 2.5) to millicores."""
    cpu = str(cpu)
    if cpu.endswith("m"):
        return float(cpu[:-1])
    try:
        return float(cpu) * 1000
    except ValueError:
        return 0.0


def _mem_to_mib(mem: str) -> int:
    """Convert memory string to MiB."""
    mem = str(mem)
    try:
        if mem.endswith("Ki"):
            return int(mem[:-2]) // 1024
        if mem.endswith("Mi"):
            return int(mem[:-2])
        if mem.endswith("Gi"):
            return int(mem[:-2]) * 1024
        if mem.endswith("Ti"):
            return int(mem[:-2]) * 1024 * 1024
        return int(mem)
    except ValueError:
        return 0


def _format_node(n: dict) -> dict:
    meta = n.get("metadata", {})
    status = n.get("status", {})
    spec = n.get("spec", {})
    conditions = status.get("conditions", [])
    ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
    alloc = status.get("allocatable", {})
    cap = status.get("capacity", {})
    labels = meta.get("labels", {})
    node_info = status.get("nodeInfo", {})
    taints = spec.get("taints", [])

    return {
        "name": meta.get("name", ""),
        "ready": ready,
        "unschedulable": spec.get("unschedulable", False),
        "roles": _node_roles(labels),
        "os": node_info.get("operatingSystem", ""),
        "kernel": node_info.get("kernelVersion", ""),
        "container_runtime": node_info.get("containerRuntimeVersion", ""),
        "kubelet_version": node_info.get("kubeletVersion", ""),
        "allocatable_cpu_m": _cpu_to_m(alloc.get("cpu", "0")),
        "allocatable_mem_mib": _mem_to_mib(alloc.get("memory", "0")),
        "capacity_cpu_m": _cpu_to_m(cap.get("cpu", "0")),
        "capacity_mem_mib": _mem_to_mib(cap.get("memory", "0")),
        "taints": taints,
        "labels": labels,
        "created_at": meta.get("creationTimestamp", ""),
        "conditions": [
            {
                "type": c.get("type", ""),
                "status": c.get("status", ""),
                "reason": c.get("reason", ""),
                "message": c.get("message", ""),
                "last_transition": c.get("lastTransitionTime", ""),
            }
            for c in conditions
        ],
    }


def _node_roles(labels: dict) -> list[str]:
    roles = []
    for k in labels:
        if k.startswith("node-role.kubernetes.io/"):
            role = k.split("/", 1)[1]
            if role:
                roles.append(role)
    if not roles:
        roles = ["worker"]
    return roles


def _format_pod(p: dict) -> dict:
    meta = p.get("metadata", {})
    spec = p.get("spec", {})
    status = p.get("status", {})
    containers = status.get("containerStatuses", [])
    init_containers = status.get("initContainerStatuses", [])
    spec_containers = spec.get("containers", [])
    owners = meta.get("ownerReferences", [])
    ready_count = sum(1 for c in containers if c.get("ready"))
    restart_count = sum(c.get("restartCount", 0) for c in containers)

    crashloop = (
        restart_count >= 5
        or any(_container_state(c) == "CrashLoopBackOff" for c in containers)
        or any(_container_state(c) in ("OOMKilled", "Error") and c.get("restartCount", 0) > 0 for c in containers)
    )

    req_cpu_m = sum(_cpu_to_m(c.get("resources", {}).get("requests", {}).get("cpu", "0")) for c in spec_containers)
    req_mem_mib = sum(_mem_to_mib(c.get("resources", {}).get("requests", {}).get("memory", "0")) for c in spec_containers)
    lim_cpu_m = sum(_cpu_to_m(c.get("resources", {}).get("limits", {}).get("cpu", "0")) for c in spec_containers)
    lim_mem_mib = sum(_mem_to_mib(c.get("resources", {}).get("limits", {}).get("memory", "0")) for c in spec_containers)

    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "phase": status.get("phase", ""),
        "pod_ip": status.get("podIP", ""),
        "host_ip": status.get("hostIP", ""),
        "node": spec.get("nodeName", ""),
        "owner": _pod_owner(owners),
        "ready": f"{ready_count}/{len(containers)}",
        "restarts": restart_count,
        "crashloop": crashloop,
        "containers": [_format_container_status(c) for c in containers],
        "init_containers": [{"name": c.get("name", ""), "ready": c.get("ready", False), "state": _container_state(c)} for c in init_containers],
        "created_at": meta.get("creationTimestamp", ""),
        "req_cpu_m": round(req_cpu_m),
        "req_mem_mib": round(req_mem_mib),
        "lim_cpu_m": round(lim_cpu_m),
        "lim_mem_mib": round(lim_mem_mib),
    }


def _pod_owner(owners: list) -> str:
    for o in owners:
        kind = o.get("kind", "")
        name = o.get("name", "")
        if kind in ("Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet"):
            return f"{kind}/{name}"
    return ""


def _container_state(c: dict) -> str:
    state = c.get("state", {})
    if "running" in state:
        return "Running"
    if "waiting" in state:
        return state["waiting"].get("reason", "Waiting")
    if "terminated" in state:
        return f"Terminated ({state['terminated'].get('reason', '')})"
    return "Unknown"


def _format_container_status(c: dict) -> dict:
    return {
        "name": c.get("name", ""),
        "image": c.get("image", ""),
        "ready": c.get("ready", False),
        "restarts": c.get("restartCount", 0),
        "state": _container_state(c),
    }


def _format_workload(w: dict, kind: str) -> dict:
    meta = w.get("metadata", {})
    spec = w.get("spec", {})
    status = w.get("status", {})

    base = {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "kind": kind,
        "replicas": spec.get("replicas"),
        "ready_replicas": status.get("readyReplicas", 0),
        "available_replicas": status.get("availableReplicas", 0),
        "updated_replicas": status.get("updatedReplicas", 0),
        "images": [c.get("image", "") for c in spec.get("template", {}).get("spec", {}).get("containers", [])],
        "selector": spec.get("selector", {}).get("matchLabels", {}),
        "labels": meta.get("labels", {}),
        "annotations": meta.get("annotations", {}),
        "created_at": meta.get("creationTimestamp", ""),
        "raw": w,
    }

    if kind == "jobs":
        conditions = status.get("conditions", [])
        complete = any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions)
        failed = any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions)
        base.update({
            "succeeded": status.get("succeeded", 0),
            "failed_count": status.get("failed", 0),
            "active": status.get("active", 0),
            "completions": spec.get("completions"),
            "start_time": status.get("startTime", ""),
            "completion_time": status.get("completionTime", ""),
            "job_status": "Complete" if complete else "Failed" if failed else "Running" if status.get("active", 0) > 0 else "Pending",
        })
        # Override replicas with active count for display
        base["replicas"] = status.get("active", 0) + status.get("succeeded", 0)
    elif kind == "cronjobs":
        base.update({
            "schedule": spec.get("schedule", ""),
            "last_schedule_time": status.get("lastScheduleTime", ""),
            "last_successful_time": status.get("lastSuccessfulTime", ""),
            "active_jobs": len(status.get("active", [])),
            "suspend": spec.get("suspend", False),
        })
        # Images from job template
        base["images"] = [c.get("image", "") for c in spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])]

    return base


def _format_service(s: dict) -> dict:
    meta = s.get("metadata", {})
    spec = s.get("spec", {})
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "type": spec.get("type", "ClusterIP"),
        "cluster_ip": spec.get("clusterIP", ""),
        "external_ips": spec.get("externalIPs", []) or (spec.get("loadBalancerIP") and [spec["loadBalancerIP"]]) or [],
        "ports": spec.get("ports", []),
        "selector": spec.get("selector", {}),
        "created_at": meta.get("creationTimestamp", ""),
    }


def _format_pvc(p: dict) -> dict:
    meta = p.get("metadata", {})
    spec = p.get("spec", {})
    status = p.get("status", {})
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "status": status.get("phase", ""),
        "capacity": status.get("capacity", {}).get("storage", ""),
        "access_modes": spec.get("accessModes", []),
        "storage_class": spec.get("storageClassName", ""),
        "volume_name": spec.get("volumeName", ""),
        "created_at": meta.get("creationTimestamp", ""),
    }


def _format_event(e: dict) -> dict:
    meta = e.get("metadata", {})
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "type": e.get("type", "Normal"),
        "reason": e.get("reason", ""),
        "message": e.get("message", ""),
        "object": f"{e.get('involvedObject', {}).get('kind', '')}/{e.get('involvedObject', {}).get('name', '')}",
        "count": e.get("count", 1),
        "last_time": e.get("lastTimestamp") or e.get("eventTime") or "",
        "source": e.get("source", {}).get("component", ""),
    }


def _format_configmap(cm: dict) -> dict:
    meta = cm.get("metadata", {})
    data = cm.get("data") or {}
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "created_at": meta.get("creationTimestamp", ""),
        "key_count": len(data),
        "keys": list(data.keys()),
        "data": data,
    }


def _format_hpa(h: dict) -> dict:
    meta = h.get("metadata", {})
    spec = h.get("spec", {})
    status = h.get("status", {})
    ref = spec.get("scaleTargetRef", {})
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "created_at": meta.get("creationTimestamp", ""),
        "target_kind": ref.get("kind", ""),
        "target_name": ref.get("name", ""),
        "min_replicas": spec.get("minReplicas", 1),
        "max_replicas": spec.get("maxReplicas", 0),
        "current_replicas": status.get("currentReplicas", 0),
        "desired_replicas": status.get("desiredReplicas", 0),
        "current_cpu_pct": status.get("currentCPUUtilizationPercentage"),
        "target_cpu_pct": spec.get("targetCPUUtilizationPercentage"),
        "conditions": [
            {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason", "")}
            for c in status.get("conditions", [])
        ],
    }


def _format_quota(q: dict) -> dict:
    meta = q.get("metadata", {})
    status = q.get("status", {})
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "created_at": meta.get("creationTimestamp", ""),
        "hard": status.get("hard", {}),
        "used": status.get("used", {}),
    }


def _format_ingress(obj: dict) -> dict:
    meta = obj.get("metadata", {})
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    rules = spec.get("rules", [])
    tls_hosts: list[str] = []
    tls_secrets: list[str] = []
    for t in spec.get("tls", []):
        tls_hosts.extend(t.get("hosts", []))
        if t.get("secretName"):
            tls_secrets.append(t["secretName"])
    hosts = list({r.get("host", "") for r in rules if r.get("host")})
    paths: list[str] = []
    for r in rules:
        for p in (r.get("http") or {}).get("paths", []):
            path = p.get("path", "/")
            svc = p.get("backend", {}).get("service", {})
            svc_name = svc.get("name", "") or p.get("backend", {}).get("serviceName", "")
            if svc_name:
                paths.append(f"{path}→{svc_name}")
            else:
                paths.append(path)
    lb_ips = [ing.get("ip", "") or ing.get("hostname", "")
              for ing in (status.get("loadBalancer") or {}).get("ingress", [])]
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "hosts": hosts,
        "tls": bool(tls_hosts),
        "tls_hosts": tls_hosts,
        "tls_secrets": tls_secrets,
        "paths": paths[:8],
        "lb_ips": [ip for ip in lb_ips if ip],
        "ingress_class": meta.get("annotations", {}).get("kubernetes.io/ingress.class", "")
                         or spec.get("ingressClassName", ""),
        "created_at": meta.get("creationTimestamp", ""),
    }


def _format_rolebinding(obj: dict) -> dict:
    meta = obj.get("metadata", {})
    role_ref = obj.get("roleRef", {})
    subjects = obj.get("subjects", []) or []
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "role_ref_kind": role_ref.get("kind", ""),
        "role_ref_name": role_ref.get("name", ""),
        "subjects": [
            {"kind": s.get("kind", ""), "name": s.get("name", ""), "namespace": s.get("namespace", "")}
            for s in subjects[:10]
        ],
        "subject_count": len(subjects),
        "created_at": meta.get("creationTimestamp", ""),
    }


def _format_serviceaccount(obj: dict) -> dict:
    meta = obj.get("metadata", {})
    secrets = obj.get("secrets", []) or []
    image_pull_secrets = obj.get("imagePullSecrets", []) or []
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "secrets_count": len(secrets),
        "image_pull_secrets": [s.get("name", "") for s in image_pull_secrets],
        "created_at": meta.get("creationTimestamp", ""),
    }


def _netpol_peer(peer: dict) -> str:
    parts = []
    if "podSelector" in peer:
        labels = peer["podSelector"].get("matchLabels", {})
        parts.append("pod:" + (",".join(f"{k}={v}" for k, v in labels.items()) or "any"))
    if "namespaceSelector" in peer:
        labels = peer["namespaceSelector"].get("matchLabels", {})
        parts.append("ns:" + (",".join(f"{k}={v}" for k, v in labels.items()) or "any"))
    if "ipBlock" in peer:
        parts.append(f"ip:{peer['ipBlock'].get('cidr', '?')}")
    return " & ".join(parts) if parts else "any"


def _netpol_rule_summary(rule: dict, direction: str) -> dict:
    peers_key = "from" if direction == "ingress" else "to"
    peers = [_netpol_peer(p) for p in rule.get(peers_key, [])] or ["any"]
    ports = [f"{p.get('port', '*')}/{p.get('protocol', 'TCP')}"
             for p in rule.get("ports", [])] or ["any"]
    return {"peers": peers, "ports": ports}


def _format_networkpolicy(obj: dict) -> dict:
    meta = obj.get("metadata", {})
    spec = obj.get("spec", {})
    pod_sel = spec.get("podSelector", {})
    ingress = spec.get("ingress", [])
    egress = spec.get("egress", [])
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "pod_selector": pod_sel.get("matchLabels", {}),
        "ingress_rules": len(ingress),
        "egress_rules": len(egress),
        "ingress_detail": [_netpol_rule_summary(r, "ingress") for r in ingress],
        "egress_detail": [_netpol_rule_summary(r, "egress") for r in egress],
        "policy_types": spec.get("policyTypes", []),
        "created_at": meta.get("creationTimestamp", ""),
    }


def _format_pdb(obj: dict) -> dict:
    meta = obj.get("metadata", {})
    spec = obj.get("spec", {})
    status = obj.get("status", {})
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "selector": spec.get("selector", {}).get("matchLabels", {}),
        "min_available": spec.get("minAvailable"),
        "max_unavailable": spec.get("maxUnavailable"),
        "current_healthy": status.get("currentHealthy", 0),
        "desired_healthy": status.get("desiredHealthy", 0),
        "disruptions_allowed": status.get("disruptionsAllowed", 0),
        "expected_pods": status.get("expectedPods", 0),
        "created_at": meta.get("creationTimestamp", ""),
    }


def _format_limitrange(obj: dict) -> dict:
    meta = obj.get("metadata", {})
    limits = []
    for lim in obj.get("spec", {}).get("limits", []):
        limits.append({
            "type": lim.get("type", ""),
            "default": lim.get("default", {}),
            "default_request": lim.get("defaultRequest", {}),
            "max": lim.get("max", {}),
            "min": lim.get("min", {}),
            "max_limit_request_ratio": lim.get("maxLimitRequestRatio", {}),
        })
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "limits": limits,
        "created_at": meta.get("creationTimestamp", ""),
    }


def _format_generic(obj: dict) -> dict:
    meta = obj.get("metadata", {})
    return {
        "name": meta.get("name", ""),
        "namespace": meta.get("namespace", ""),
        "labels": meta.get("labels", {}),
        "annotations": {k: v for k, v in (meta.get("annotations") or {}).items()
                        if not k.startswith("kubectl.kubernetes.io")},
        "created_at": meta.get("creationTimestamp", ""),
        "raw": obj,
    }


# ── Loop 50: Scheduling Issues / Pending Pod Analyzer ────────────────────────

_SCHED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Insufficient\s+cpu",                                           re.IGNORECASE), "insufficient_cpu"),
    (re.compile(r"Insufficient\s+memory",                                        re.IGNORECASE), "insufficient_memory"),
    (re.compile(r"0/\d+ nodes are available.*Insufficient memory",               re.IGNORECASE), "insufficient_memory"),
    (re.compile(r"0/\d+ nodes are available.*Insufficient cpu",                  re.IGNORECASE), "insufficient_cpu"),
    (re.compile(r"node\(s\) had untolerated taint|node\(s\) had taint",          re.IGNORECASE), "taint_toleration"),
    (re.compile(r"didn.t match.*node affinity|node\(s\).*node affinity",         re.IGNORECASE), "affinity_mismatch"),
    (re.compile(r"didn.t match.*selector|nodeSelector|node selector",            re.IGNORECASE), "no_matching_node"),
    (re.compile(r"unbound.*persistentvolumeclaim|pvc.*unbound|persistentvolume", re.IGNORECASE), "pvc_pending"),
    (re.compile(r"0/\d+ nodes are available",                                    re.IGNORECASE), "no_nodes_available"),
]

_SCHED_PRIORITY = {
    "image_pull": 0, "no_nodes_available": 1,
    "insufficient_cpu": 2, "insufficient_memory": 3,
    "taint_toleration": 4, "affinity_mismatch": 5,
    "no_matching_node": 6, "pvc_pending": 7,
    "other": 8, "unknown": 9,
}


def _categorize_sched_message(msg: str) -> str:
    for pattern, cat in _SCHED_PATTERNS:
        if pattern.search(msg):
            return cat
    return "other"


@app.get("/clusters/{cluster_id:path}/scheduling-issues")
async def scheduling_issues(cluster_id: str, namespace: str = ""):
    """List Pending pods with categorised scheduling failure reasons."""
    client = await _cluster(cluster_id)

    pods_raw, events_raw = await asyncio.gather(
        kube_list(client, "pods", namespace or None),
        kube_list(client, "events", namespace or None),
    )

    # Index Warning events by involved Pod name
    events_by_pod: dict[str, list[dict]] = {}
    for ev in events_raw:
        if ev.get("type") == "Warning":
            obj = ev.get("involvedObject", {})
            if obj.get("kind") == "Pod":
                events_by_pod.setdefault(obj.get("name", ""), []).append(ev)

    pending_pods: list[dict] = []
    category_counts: dict[str, int] = {}

    for pod in pods_raw:
        if pod.get("status", {}).get("phase") != "Pending":
            continue

        meta = pod.get("metadata", {})
        pod_name = meta.get("name", "")
        pod_ns   = meta.get("namespace", "")

        # Prefer PodScheduled condition message
        conditions = pod.get("status", {}).get("conditions", [])
        sched_cond = next((c for c in conditions if c.get("type") == "PodScheduled"), None)
        sched_message = ""
        if sched_cond and sched_cond.get("status") == "False":
            sched_message = sched_cond.get("message", "")

        # Fallback to most recent FailedScheduling / FailedMount event
        if not sched_message:
            pod_evs = sorted(
                events_by_pod.get(pod_name, []),
                key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "",
                reverse=True,
            )
            for ev in pod_evs:
                if ev.get("reason") in ("FailedScheduling", "Unschedulable", "FailedMount"):
                    sched_message = ev.get("message", "")
                    break

        # Detect image pull failures from container statuses
        image_issues: list[dict] = []
        all_cs = (pod.get("status", {}).get("containerStatuses") or []) + \
                 (pod.get("status", {}).get("initContainerStatuses") or [])
        for cs in all_cs:
            waiting = cs.get("state", {}).get("waiting", {})
            reason  = waiting.get("reason", "")
            if reason in ("ImagePullBackOff", "ErrImagePull", "InvalidImageName", "ErrImageNeverPull"):
                image_issues.append({
                    "container": cs.get("name", ""),
                    "reason": reason,
                    "message": waiting.get("message", ""),
                })

        if image_issues:
            category = "image_pull"
        elif sched_message:
            category = _categorize_sched_message(sched_message)
        else:
            category = "unknown"

        category_counts[category] = category_counts.get(category, 0) + 1

        spec = pod.get("spec", {})
        containers = spec.get("containers", [])
        tolerations = spec.get("tolerations") or []

        pending_pods.append({
            "name":          pod_name,
            "namespace":     pod_ns,
            "created_at":    meta.get("creationTimestamp", ""),
            "category":      category,
            "message":       sched_message[:600] if sched_message else "",
            "images":        [c.get("image", "") for c in containers],
            "node_selector": spec.get("nodeSelector") or {},
            "tolerations":   [{"key": t.get("key", ""), "effect": t.get("effect", "")} for t in tolerations if t.get("key")],
            "image_issues":  image_issues,
        })

    pending_pods.sort(key=lambda p: (_SCHED_PRIORITY.get(p["category"], 8), p["name"]))

    return {
        "pending_pods":  pending_pods,
        "total":         len(pending_pods),
        "categories":    category_counts,
    }


# ── Loop 51: RBAC Risk Auditor ────────────────────────────────────────────────

_SENSITIVE_RESOURCES = frozenset({
    "secrets", "pods/exec", "pods/attach", "nodes", "clusterroles",
    "clusterrolebindings", "rolebindings", "roles",
    "persistentvolumes", "namespaces", "serviceaccounts/token",
})

_SENSITIVE_VERBS = frozenset({"create", "update", "patch", "delete", "deletecollection", "*"})


def _rbac_risk_level(rules: list[dict]) -> str:
    """Return 'critical' | 'high' | 'medium' | 'low' based on rules."""
    for rule in rules:
        verbs = set(rule.get("verbs", []))
        resources = set(rule.get("resources", []))
        api_groups = set(rule.get("apiGroups", []))
        if "*" in verbs and "*" in resources:
            return "critical"
        if "*" in resources and verbs & _SENSITIVE_VERBS:
            return "critical"
        if "secrets" in resources and verbs & _SENSITIVE_VERBS:
            return "critical"
        if "*" in verbs and resources & _SENSITIVE_RESOURCES:
            return "high"
        if resources & _SENSITIVE_RESOURCES and verbs & _SENSITIVE_VERBS:
            return "high"
        if "*" in verbs:
            return "high"
    return "medium"


def _analyze_role_rules(rules: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for rule in rules:
        verbs = rule.get("verbs", [])
        resources = rule.get("resources", [])
        api_groups = rule.get("apiGroups", [])

        if "*" in verbs and "*" in resources:
            findings.append({"type": "wildcard_all", "detail": "Full wildcard: verbs=* resources=*", "severity": "critical"})
        elif "*" in verbs:
            findings.append({"type": "wildcard_verbs", "detail": f"Wildcard verbs on: {', '.join(resources)}", "severity": "high"})
        elif "*" in resources:
            findings.append({"type": "wildcard_resources", "detail": f"Wildcard resources with verbs: {', '.join(verbs)}", "severity": "high"})

        for res in resources:
            if res == "secrets" and set(verbs) & _SENSITIVE_VERBS:
                findings.append({"type": "secrets_write", "detail": f"Write access to secrets (verbs: {', '.join(verbs)})", "severity": "critical"})
            elif res in ("pods/exec", "pods/attach"):
                findings.append({"type": "pod_exec", "detail": f"Pod exec/attach permission (verbs: {', '.join(verbs)})", "severity": "high"})
    return findings


@app.get("/clusters/{cluster_id:path}/rbac/risks")
async def rbac_risks(cluster_id: str, namespace: str = ""):
    """Scan RBAC for high-risk patterns: cluster-admin grants, wildcards, sensitive resource access."""
    client = await _cluster(cluster_id)

    croles_r, crbs_r, roles_r, rbs_r = await asyncio.gather(
        kube_list(client, "clusterroles"),
        kube_list(client, "clusterrolebindings"),
        kube_list(client, "roles", namespace or None),
        kube_list(client, "rolebindings", namespace or None),
        return_exceptions=True,
    )
    croles     = croles_r     if not isinstance(croles_r, Exception)     else []
    crbs       = crbs_r       if not isinstance(crbs_r, Exception)       else []
    roles      = roles_r      if not isinstance(roles_r, Exception)      else []
    rolebindings = rbs_r      if not isinstance(rbs_r, Exception)        else []

    # Index roles by name for fast lookup
    crole_map: dict[str, dict] = {r.get("metadata", {}).get("name", ""): r for r in croles}
    role_map: dict[str, dict]  = {
        f"{r.get('metadata', {}).get('namespace', '')}/{r.get('metadata', {}).get('name', '')}": r
        for r in roles
    }

    risks: list[dict] = []

    # ── ClusterRoleBindings ───────────────────────────────────────────────────
    for crb in crbs:
        meta      = crb.get("metadata", {})
        role_ref  = crb.get("roleRef", {})
        subjects  = crb.get("subjects", []) or []
        role_name = role_ref.get("name", "")
        rb_name   = meta.get("name", "")

        # cluster-admin grant
        if role_name == "cluster-admin":
            for subj in subjects:
                risks.append({
                    "severity": "critical",
                    "type": "cluster_admin_grant",
                    "binding": rb_name,
                    "binding_kind": "ClusterRoleBinding",
                    "namespace": "",
                    "role": "cluster-admin",
                    "subject_kind": subj.get("kind", ""),
                    "subject_name": subj.get("name", ""),
                    "subject_namespace": subj.get("namespace", ""),
                    "findings": [{"type": "cluster_admin", "detail": "Subject holds cluster-admin", "severity": "critical"}],
                })
            continue

        # system:masters group
        for subj in subjects:
            if subj.get("kind") == "Group" and subj.get("name") == "system:masters":
                risks.append({
                    "severity": "critical",
                    "type": "system_masters_grant",
                    "binding": rb_name,
                    "binding_kind": "ClusterRoleBinding",
                    "namespace": "",
                    "role": role_name,
                    "subject_kind": "Group",
                    "subject_name": "system:masters",
                    "subject_namespace": "",
                    "findings": [{"type": "system_masters", "detail": "Member of system:masters group", "severity": "critical"}],
                })

        # Analyse referenced ClusterRole rules
        crole = crole_map.get(role_name)
        if crole:
            rules = crole.get("rules", []) or []
            findings = _analyze_role_rules(rules)
            if findings:
                max_sev = "critical" if any(f["severity"] == "critical" for f in findings) else "high"
                for subj in subjects:
                    risks.append({
                        "severity": max_sev,
                        "type": "risky_clusterrole",
                        "binding": rb_name,
                        "binding_kind": "ClusterRoleBinding",
                        "namespace": "",
                        "role": role_name,
                        "subject_kind": subj.get("kind", ""),
                        "subject_name": subj.get("name", ""),
                        "subject_namespace": subj.get("namespace", ""),
                        "findings": findings,
                    })

    # ── RoleBindings ──────────────────────────────────────────────────────────
    for rb in rolebindings:
        meta      = rb.get("metadata", {})
        role_ref  = rb.get("roleRef", {})
        subjects  = rb.get("subjects", []) or []
        role_name = role_ref.get("name", "")
        rb_ns     = meta.get("namespace", "")
        rb_name   = meta.get("name", "")

        # cluster-admin via RoleBinding (scoped to namespace)
        if role_name == "cluster-admin":
            for subj in subjects:
                risks.append({
                    "severity": "high",
                    "type": "cluster_admin_grant",
                    "binding": rb_name,
                    "binding_kind": "RoleBinding",
                    "namespace": rb_ns,
                    "role": "cluster-admin",
                    "subject_kind": subj.get("kind", ""),
                    "subject_name": subj.get("name", ""),
                    "subject_namespace": subj.get("namespace", ""),
                    "findings": [{"type": "cluster_admin", "detail": "Namespace-scoped cluster-admin grant", "severity": "high"}],
                })
            continue

        ref_kind = role_ref.get("kind", "")
        if ref_kind == "ClusterRole":
            role_obj = crole_map.get(role_name)
        else:
            role_obj = role_map.get(f"{rb_ns}/{role_name}")

        if role_obj:
            rules = role_obj.get("rules", []) or []
            findings = _analyze_role_rules(rules)
            if findings:
                max_sev = "critical" if any(f["severity"] == "critical" for f in findings) else "high"
                for subj in subjects:
                    # default SA with risky permissions
                    subj_name = subj.get("name", "")
                    if subj.get("kind") == "ServiceAccount" and subj_name == "default":
                        max_sev = max_sev  # keep severity, mark as notable
                    risks.append({
                        "severity": max_sev,
                        "type": "risky_role",
                        "binding": rb_name,
                        "binding_kind": "RoleBinding",
                        "namespace": rb_ns,
                        "role": role_name,
                        "subject_kind": subj.get("kind", ""),
                        "subject_name": subj_name,
                        "subject_namespace": subj.get("namespace", ""),
                        "findings": findings,
                    })

    # Deduplicate (same binding may appear multiple times for multiple subjects — keep all)
    # Sort: critical first, then high, then by binding name
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    risks.sort(key=lambda r: (sev_order.get(r["severity"], 9), r["binding"]))

    summary = {
        "total": len(risks),
        "critical": sum(1 for r in risks if r["severity"] == "critical"),
        "high": sum(1 for r in risks if r["severity"] == "high"),
        "medium": sum(1 for r in risks if r["severity"] == "medium"),
    }

    return {"risks": risks, "summary": summary}


# ── Loop 52: Node Resource Pressure Dashboard ─────────────────────────────────

@app.get("/clusters/{cluster_id:path}/nodes/pressure")
async def node_pressure(cluster_id: str):
    """Per-node CPU & memory allocation pressure: requested vs allocatable."""
    client = await _cluster(cluster_id)

    nodes_raw, pods_raw, metrics_raw = await asyncio.gather(
        kube_list(client, "nodes"),
        kube_list(client, "pods"),
        pod_metrics(client),
        return_exceptions=True,
    )
    nodes_raw   = nodes_raw   if not isinstance(nodes_raw,   Exception) else []
    pods_raw    = pods_raw    if not isinstance(pods_raw,    Exception) else []
    metrics_raw = metrics_raw if not isinstance(metrics_raw, Exception) else {"available": False, "pods": []}

    # Build live usage index: pod_name -> {cpu_m, mem_mib}
    live_usage: dict[str, dict] = {}
    if isinstance(metrics_raw, dict) and metrics_raw.get("available"):
        for pm in metrics_raw.get("pods", []):
            containers = pm.get("containers", [])
            cpu_m  = sum(_cpu_to_m(c.get("usage", {}).get("cpu", "0"))  for c in containers)
            mem_mib = sum(_mem_to_mib(c.get("usage", {}).get("memory", "0")) for c in containers)
            live_usage[pm.get("name", "")] = {"cpu_m": cpu_m, "mem_mib": mem_mib}

    # Aggregate pod requests/limits/live per node
    node_pods: dict[str, list[dict]] = {}
    for pod in pods_raw:
        if pod.get("status", {}).get("phase") not in ("Running", "Pending"):
            continue
        node_name = pod.get("spec", {}).get("nodeName", "")
        if not node_name:
            continue
        meta = pod.get("metadata", {})
        pod_name = meta.get("name", "")
        containers = pod.get("spec", {}).get("containers", []) + pod.get("spec", {}).get("initContainers", [])

        req_cpu_m = req_mem_mib = lim_cpu_m = lim_mem_mib = 0
        for c in containers:
            res = c.get("resources", {})
            req_cpu_m   += _cpu_to_m(res.get("requests", {}).get("cpu", "0"))
            req_mem_mib += _mem_to_mib(res.get("requests", {}).get("memory", "0"))
            lim_cpu_m   += _cpu_to_m(res.get("limits", {}).get("cpu", "0"))
            lim_mem_mib += _mem_to_mib(res.get("limits", {}).get("memory", "0"))

        live = live_usage.get(pod_name, {})
        node_pods.setdefault(node_name, []).append({
            "name": pod_name,
            "namespace": meta.get("namespace", ""),
            "req_cpu_m": round(req_cpu_m, 1),
            "req_mem_mib": req_mem_mib,
            "lim_cpu_m": round(lim_cpu_m, 1),
            "lim_mem_mib": lim_mem_mib,
            "live_cpu_m": round(live.get("cpu_m", 0), 1),
            "live_mem_mib": live.get("mem_mib", 0),
        })

    nodes_out: list[dict] = []
    for n in nodes_raw:
        meta   = n.get("metadata", {})
        name   = meta.get("name", "")
        status = n.get("status", {})
        alloc  = status.get("allocatable", {})
        cond   = status.get("conditions", [])

        alloc_cpu_m   = _cpu_to_m(alloc.get("cpu", "0"))
        alloc_mem_mib = _mem_to_mib(alloc.get("memory", "0"))

        is_ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in cond)

        pods = node_pods.get(name, [])
        total_req_cpu_m   = sum(p["req_cpu_m"]   for p in pods)
        total_req_mem_mib = sum(p["req_mem_mib"] for p in pods)
        total_lim_cpu_m   = sum(p["lim_cpu_m"]   for p in pods)
        total_lim_mem_mib = sum(p["lim_mem_mib"] for p in pods)
        total_live_cpu_m  = sum(p["live_cpu_m"]  for p in pods)
        total_live_mem_mib = sum(p["live_mem_mib"] for p in pods)

        cpu_req_pct = round(total_req_cpu_m / alloc_cpu_m * 100, 1) if alloc_cpu_m else 0
        mem_req_pct = round(total_req_mem_mib / alloc_mem_mib * 100, 1) if alloc_mem_mib else 0
        cpu_lim_pct = round(total_lim_cpu_m / alloc_cpu_m * 100, 1) if alloc_cpu_m else 0
        mem_lim_pct = round(total_lim_mem_mib / alloc_mem_mib * 100, 1) if alloc_mem_mib else 0
        cpu_live_pct = round(total_live_cpu_m / alloc_cpu_m * 100, 1) if alloc_cpu_m else 0
        mem_live_pct = round(total_live_mem_mib / alloc_mem_mib * 100, 1) if alloc_mem_mib else 0

        # Pressure level
        cpu_over = cpu_req_pct > 100
        mem_over = mem_req_pct > 100
        if not is_ready:
            pressure = "not_ready"
        elif cpu_over or mem_over:
            pressure = "over_committed"
        elif cpu_req_pct >= 80 or mem_req_pct >= 80:
            pressure = "high"
        elif cpu_req_pct >= 50 or mem_req_pct >= 50:
            pressure = "medium"
        else:
            pressure = "low"

        # Sort pods by req cpu desc for top consumer display
        pods.sort(key=lambda p: p["req_cpu_m"], reverse=True)

        node_info = n.get("info", status.get("nodeInfo", {}))
        labels    = meta.get("labels", {}) or {}
        roles: list[str] = []
        for lk, lv in labels.items():
            if lk.startswith("node-role.kubernetes.io/"):
                roles.append(lk.split("/", 1)[1])

        nodes_out.append({
            "name":           name,
            "ready":          is_ready,
            "pressure":       pressure,
            "roles":          roles,
            "os_image":       status.get("nodeInfo", {}).get("osImage", ""),
            "kernel":         status.get("nodeInfo", {}).get("kernelVersion", ""),
            "pod_count":      len(pods),
            "alloc_cpu_m":    round(alloc_cpu_m, 1),
            "alloc_mem_mib":  alloc_mem_mib,
            "req_cpu_m":      round(total_req_cpu_m, 1),
            "req_mem_mib":    total_req_mem_mib,
            "lim_cpu_m":      round(total_lim_cpu_m, 1),
            "lim_mem_mib":    total_lim_mem_mib,
            "live_cpu_m":     round(total_live_cpu_m, 1),
            "live_mem_mib":   total_live_mem_mib,
            "cpu_req_pct":    cpu_req_pct,
            "mem_req_pct":    mem_req_pct,
            "cpu_lim_pct":    cpu_lim_pct,
            "mem_lim_pct":    mem_lim_pct,
            "cpu_live_pct":   cpu_live_pct,
            "mem_live_pct":   mem_live_pct,
            "top_pods":       pods[:10],
        })

    # Sort: not_ready first, then over_committed, then by cpu_req_pct desc
    pressure_order = {"not_ready": 0, "over_committed": 1, "high": 2, "medium": 3, "low": 4}
    nodes_out.sort(key=lambda n: (pressure_order.get(n["pressure"], 5), -n["cpu_req_pct"]))

    total_alloc_cpu  = sum(n["alloc_cpu_m"]   for n in nodes_out)
    total_alloc_mem  = sum(n["alloc_mem_mib"]  for n in nodes_out)
    total_req_cpu    = sum(n["req_cpu_m"]      for n in nodes_out)
    total_req_mem    = sum(n["req_mem_mib"]    for n in nodes_out)

    return {
        "nodes": nodes_out,
        "metrics_available": isinstance(metrics_raw, dict) and metrics_raw.get("available", False),
        "cluster": {
            "total_nodes":     len(nodes_out),
            "ready_nodes":     sum(1 for n in nodes_out if n["ready"]),
            "over_committed":  sum(1 for n in nodes_out if n["pressure"] == "over_committed"),
            "high_pressure":   sum(1 for n in nodes_out if n["pressure"] == "high"),
            "total_alloc_cpu_m":  round(total_alloc_cpu, 1),
            "total_alloc_mem_mib": total_alloc_mem,
            "total_req_cpu_m":    round(total_req_cpu, 1),
            "total_req_mem_mib":  total_req_mem,
            "cluster_cpu_req_pct": round(total_req_cpu / total_alloc_cpu * 100, 1) if total_alloc_cpu else 0,
            "cluster_mem_req_pct": round(total_req_mem / total_alloc_mem * 100, 1) if total_alloc_mem else 0,
        },
    }


# ── Loop 53: Cross-Pod Log Search ─────────────────────────────────────────────

async def _search_pod_logs(
    client,
    pod_name: str,
    namespace: str,
    q: str,
    tail: int,
    container: str = "",
) -> list[dict]:
    """Return lines from a single pod that contain q (case-insensitive)."""
    try:
        params: dict = {"tailLines": min(tail, 2000)}
        if container:
            params["container"] = container
        resp = await client.get(
            f"/api/v1/namespaces/{namespace}/pods/{pod_name}/log",
            params=params,
        )
        if resp.status_code != 200:
            return []
        q_lower = q.lower()
        matches: list[dict] = []
        for lineno, line in enumerate(resp.text.splitlines(), start=1):
            if q_lower in line.lower():
                matches.append({
                    "line_no": lineno,
                    "line": line[:500],
                })
                if len(matches) >= 50:
                    break
        return matches
    except Exception:
        return []


@app.get("/clusters/{cluster_id:path}/log-search")
async def log_search(
    cluster_id: str,
    q: str = "",
    namespace: str = "",
    pods: str = "",
    tail: int = Query(default=500, le=2000),
):
    """Search for a string across pod logs in a namespace."""
    if len(q.strip()) < 2:
        raise HTTPException(status_code=422, detail="q must be at least 2 characters")

    client = await _cluster(cluster_id)

    if pods:
        pod_names = [p.strip() for p in pods.split(",") if p.strip()]
        # Validate namespace required if pods specified without namespace
        if not namespace:
            raise HTTPException(status_code=422, detail="namespace required when pods are specified")
    else:
        if not namespace:
            raise HTTPException(status_code=422, detail="namespace is required")
        pods_raw = await kube_list(client, "pods", namespace)
        pod_names = [
            p.get("metadata", {}).get("name", "")
            for p in pods_raw
            if p.get("status", {}).get("phase") in ("Running", "Pending")
        ]

    if len(pod_names) > 50:
        pod_names = pod_names[:50]

    tasks = [
        _search_pod_logs(client, name, namespace, q.strip(), tail)
        for name in pod_names
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[dict] = []
    total_matches = 0
    for name, res in zip(pod_names, results_list):
        if isinstance(res, Exception) or not res:
            continue
        total_matches += len(res)
        results.append({
            "pod": name,
            "namespace": namespace,
            "match_count": len(res),
            "matches": res,
        })

    results.sort(key=lambda r: r["match_count"], reverse=True)

    return {
        "query": q.strip(),
        "namespace": namespace,
        "pods_searched": len(pod_names),
        "pods_with_matches": len(results),
        "total_matches": total_matches,
        "results": results,
    }


# ── Loop 54: PVC Analysis ─────────────────────────────────────────────────────

@app.get("/clusters/{cluster_id:path}/pvcs/analysis")
async def pvc_analysis(cluster_id: str, namespace: str = ""):
    """Deep analysis of PVC health: orphaned, pending, multi-mount, access-mode issues."""
    client = await _cluster(cluster_id)
    ns = namespace or None

    pvcs_r, pods_r = await asyncio.gather(
        kube_list(client, "pvcs", ns),
        kube_list(client, "pods", ns),
        return_exceptions=True,
    )
    pvcs: list[dict] = pvcs_r if not isinstance(pvcs_r, Exception) else []
    pods: list[dict] = pods_r if not isinstance(pods_r, Exception) else []

    # Build map: (namespace, pvc_name) → list of pod names mounting it
    pvc_mounts: dict[tuple[str, str], list[str]] = {}
    for pod in pods:
        pod_ns = pod.get("metadata", {}).get("namespace", "")
        pod_name = pod.get("metadata", {}).get("name", "")
        for vol in pod.get("spec", {}).get("volumes", []):
            pvc_ref = vol.get("persistentVolumeClaim", {})
            if pvc_ref:
                claim = pvc_ref.get("claimName", "")
                key = (pod_ns, claim)
                pvc_mounts.setdefault(key, []).append(pod_name)

    results = []
    summary = {
        "total": 0,
        "bound": 0,
        "pending": 0,
        "lost": 0,
        "orphaned": 0,
        "multi_mount_rwo": 0,
        "total_capacity_gib": 0.0,
    }

    for pvc in pvcs:
        meta = pvc.get("metadata", {})
        spec = pvc.get("spec", {})
        status = pvc.get("status", {})
        pvc_ns = meta.get("namespace", "")
        pvc_name = meta.get("name", "")
        phase = status.get("phase", "")
        access_modes = spec.get("accessModes", [])
        storage_class = spec.get("storageClassName", "")
        capacity_str = (status.get("capacity") or spec.get("resources", {}).get("requests", {})).get("storage", "0")
        volume_name = spec.get("volumeName", "")
        created_at = meta.get("creationTimestamp", "")

        # Parse capacity to GiB
        cap_gib = _mem_to_mib(capacity_str) / 1024.0

        mounting_pods = pvc_mounts.get((pvc_ns, pvc_name), [])
        orphaned = phase == "Bound" and len(mounting_pods) == 0
        multi_mount_rwo = "ReadWriteOnce" in access_modes and len(mounting_pods) > 1

        issues: list[str] = []
        if orphaned:
            issues.append("bound_not_mounted")
        if multi_mount_rwo:
            issues.append("rwo_multi_mount")
        if phase == "Pending":
            issues.append("pending")
        if phase == "Lost":
            issues.append("lost")

        summary["total"] += 1
        summary[phase.lower()] = summary.get(phase.lower(), 0) + 1
        if orphaned:
            summary["orphaned"] += 1
        if multi_mount_rwo:
            summary["multi_mount_rwo"] += 1
        summary["total_capacity_gib"] = round(summary["total_capacity_gib"] + cap_gib, 2)

        results.append({
            "name": pvc_name,
            "namespace": pvc_ns,
            "phase": phase,
            "access_modes": access_modes,
            "storage_class": storage_class,
            "capacity": capacity_str,
            "capacity_gib": round(cap_gib, 2),
            "volume_name": volume_name,
            "created_at": created_at,
            "mounting_pods": mounting_pods,
            "mount_count": len(mounting_pods),
            "orphaned": orphaned,
            "issues": issues,
        })

    # Sort: lost first, then pending, then orphaned bound, then ok
    _phase_order = {"Lost": 0, "Pending": 1, "Bound": 2}
    results.sort(key=lambda r: (
        _phase_order.get(r["phase"], 9),
        0 if r["orphaned"] else 1,
        0 if r["issues"] else 1,
        r["name"],
    ))

    return {"pvcs": results, "summary": summary}


# ── Loop 55: Fleet Diff ───────────────────────────────────────────────────────

async def _cluster_snapshot(cluster_id: str) -> dict:
    """Collect resource snapshot for one cluster; returns error dict on failure."""
    try:
        client = await _cluster(cluster_id)
        ns_r, nodes_r, pods_r, deps_r, sts_r, pvcs_r, secs_r = await asyncio.gather(
            kube_list(client, "namespaces"),
            kube_list(client, "nodes"),
            kube_list(client, "pods"),
            kube_list(client, "deployments"),
            kube_list(client, "statefulsets"),
            kube_list(client, "pvcs"),
            kube_list(client, "secrets"),
            return_exceptions=True,
        )
        def _safe(v): return v if not isinstance(v, Exception) else []
        namespaces = _safe(ns_r)
        nodes = _safe(nodes_r)
        pods = _safe(pods_r)
        deployments = _safe(deps_r)
        statefulsets = _safe(sts_r)
        pvcs = _safe(pvcs_r)
        secrets = _safe(secs_r)

        ready_nodes = sum(1 for n in nodes if any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in n.get("status", {}).get("conditions", [])))

        alloc_cpu_cores = sum(
            _cpu_to_m(n.get("status", {}).get("allocatable", {}).get("cpu", "0")) / 1000
            for n in nodes)
        alloc_mem_gib = sum(
            _mem_to_mib(n.get("status", {}).get("allocatable", {}).get("memory", "0"))
            for n in nodes) / 1024

        running = sum(1 for p in pods if p.get("status", {}).get("phase") == "Running")
        pending = sum(1 for p in pods if p.get("status", {}).get("phase") == "Pending")
        failed  = sum(1 for p in pods if p.get("status", {}).get("phase") == "Failed")

        dep_ready    = sum(1 for d in deployments if (d.get("status", {}).get("readyReplicas") or 0) >= (d.get("spec", {}).get("replicas") or 0) > 0)
        dep_degraded = sum(1 for d in deployments if (d.get("spec", {}).get("replicas") or 0) > 0 and (d.get("status", {}).get("readyReplicas") or 0) < (d.get("spec", {}).get("replicas") or 0))

        pvc_bound   = sum(1 for p in pvcs if p.get("status", {}).get("phase") == "Bound")
        pvc_pending_count = sum(1 for p in pvcs if p.get("status", {}).get("phase") == "Pending")
        total_cap_gib = sum(_mem_to_mib(
            (p.get("status", {}).get("capacity") or {}).get("storage", "0")) / 1024
            for p in pvcs if p.get("status", {}).get("phase") == "Bound")

        user_ns = [n for n in namespaces if not n.get("metadata", {}).get("name", "").startswith(
            ("kube-", "cert-manager", "capi", "tkg-", "vmware-", "velero", "secretgen", "linkerd"))]

        return {
            "cluster_id": cluster_id,
            "reachable": True,
            "namespace_count": len(user_ns),
            "total_namespaces": len(namespaces),
            "node_count": len(nodes),
            "ready_nodes": ready_nodes,
            "alloc_cpu_cores": round(alloc_cpu_cores, 2),
            "alloc_mem_gib": round(alloc_mem_gib, 1),
            "pod_total": len(pods),
            "pod_running": running,
            "pod_pending": pending,
            "pod_failed": failed,
            "deployment_count": len(deployments),
            "deployment_ready": dep_ready,
            "deployment_degraded": dep_degraded,
            "statefulset_count": len(statefulsets),
            "pvc_count": len(pvcs),
            "pvc_bound": pvc_bound,
            "pvc_pending": pvc_pending_count,
            "storage_gib": round(total_cap_gib, 1),
            "secret_count": len(secrets),
        }
    except Exception as exc:
        return {"cluster_id": cluster_id, "reachable": False, "error": str(exc)}


@app.get("/fleet/diff")
async def fleet_diff(a: str, b: str):
    """Return side-by-side resource snapshots for two clusters for comparison."""
    if not a or not b:
        from fastapi import HTTPException
        raise HTTPException(400, "Both 'a' and 'b' cluster IDs are required")
    snap_a, snap_b = await asyncio.gather(_cluster_snapshot(a), _cluster_snapshot(b))

    # Build diff rows for numeric fields
    _LABELS: list[tuple[str, str]] = [
        ("namespace_count", "User Namespaces"),
        ("node_count", "Nodes"),
        ("ready_nodes", "Ready Nodes"),
        ("alloc_cpu_cores", "Allocatable CPU (cores)"),
        ("alloc_mem_gib", "Allocatable Memory (GiB)"),
        ("pod_total", "Pods Total"),
        ("pod_running", "Pods Running"),
        ("pod_pending", "Pods Pending"),
        ("pod_failed", "Pods Failed"),
        ("deployment_count", "Deployments"),
        ("deployment_ready", "Deployments Ready"),
        ("deployment_degraded", "Deployments Degraded"),
        ("statefulset_count", "StatefulSets"),
        ("pvc_count", "PVCs"),
        ("pvc_bound", "PVCs Bound"),
        ("storage_gib", "Storage Bound (GiB)"),
        ("secret_count", "Secrets"),
    ]

    diff_rows = []
    for key, label in _LABELS:
        val_a = snap_a.get(key)
        val_b = snap_b.get(key)
        if val_a is None and val_b is None:
            continue
        delta = None
        if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
            delta = round(val_b - val_a, 2)
        diff_rows.append({
            "metric": label,
            "key": key,
            "a": val_a,
            "b": val_b,
            "delta": delta,
        })

    return {"cluster_a": snap_a, "cluster_b": snap_b, "diff": diff_rows}


# ── Loop 56: Workload Restart Timeline ───────────────────────────────────────

@app.get("/clusters/{cluster_id:path}/workloads/restart-timeline")
async def workload_restart_timeline(cluster_id: str, namespace: str = "", min_restarts: int = 0):
    """Aggregate restart counts for all workloads; highlights frequently-restarting pods."""
    client = await _cluster(cluster_id)
    ns = namespace or None

    pods_r, deps_r, sts_r, dsets_r = await asyncio.gather(
        kube_list(client, "pods", ns),
        kube_list(client, "deployments", ns),
        kube_list(client, "statefulsets", ns),
        kube_list(client, "daemonsets", ns),
        return_exceptions=True,
    )

    def _safe(v): return v if not isinstance(v, Exception) else []
    pods   = _safe(pods_r)
    deps   = _safe(deps_r)
    sts    = _safe(sts_r)
    dsets  = _safe(dsets_r)

    # Build owner-name map for pods: pod_name → (kind, workload_name, namespace)
    # Pods owned by ReplicaSets need further resolution via RS → Deployment
    owner_cache: dict[str, tuple[str, str, str]] = {}

    rs_owner: dict[tuple[str, str], str] = {}  # (rs_name, ns) → deployment_name
    for dep in deps:
        dep_meta = dep.get("metadata", {})
        dep_name = dep_meta.get("name", "")
        dep_ns   = dep_meta.get("namespace", "")
        dep_selector = dep.get("spec", {}).get("selector", {}).get("matchLabels", {})
        for pod in pods:
            pod_meta = pod.get("metadata", {})
            pod_labels = pod_meta.get("labels", {})
            if all(pod_labels.get(k) == v for k, v in dep_selector.items()):
                pod_name = pod_meta.get("name", "")
                owner_cache[pod_name] = ("Deployment", dep_name, dep_ns)

    for s in sts:
        s_meta = s.get("metadata", {})
        s_name = s_meta.get("name", "")
        s_ns   = s_meta.get("namespace", "")
        s_selector = s.get("spec", {}).get("selector", {}).get("matchLabels", {})
        for pod in pods:
            pod_meta = pod.get("metadata", {})
            pod_labels = pod_meta.get("labels", {})
            if all(pod_labels.get(k) == v for k, v in s_selector.items()):
                owner_cache[pod_meta.get("name", "")] = ("StatefulSet", s_name, s_ns)

    for ds in dsets:
        ds_meta = ds.get("metadata", {})
        ds_name = ds_meta.get("name", "")
        ds_ns   = ds_meta.get("namespace", "")
        ds_selector = ds.get("spec", {}).get("selector", {}).get("matchLabels", {})
        for pod in pods:
            pod_meta = pod.get("metadata", {})
            pod_labels = pod_meta.get("labels", {})
            if all(pod_labels.get(k) == v for k, v in ds_selector.items()):
                owner_cache[pod_meta.get("name", "")] = ("DaemonSet", ds_name, ds_ns)

    # Aggregate by workload
    from collections import defaultdict
    WorkloadKey = tuple  # (kind, name, namespace)
    workload_restarts: dict[WorkloadKey, dict] = defaultdict(lambda: {
        "total_restarts": 0, "pod_details": [], "last_restart": ""
    })

    for pod in pods:
        pod_meta = pod.get("metadata", {})
        pod_name = pod_meta.get("name", "")
        pod_ns   = pod_meta.get("namespace", "")
        container_statuses = pod.get("status", {}).get("containerStatuses", [])

        pod_restarts = 0
        pod_last_restart = ""
        for cs in container_statuses:
            count = cs.get("restartCount", 0)
            pod_restarts += count
            # Last restart time from lastState.terminated.finishedAt
            finished = cs.get("lastState", {}).get("terminated", {}).get("finishedAt", "")
            if finished and (not pod_last_restart or finished > pod_last_restart):
                pod_last_restart = finished

        if pod_restarts < 1 and min_restarts > 0:
            continue

        owner = owner_cache.get(pod_name)
        if owner:
            kind, wl_name, wl_ns = owner
        else:
            # Standalone pod
            kind, wl_name, wl_ns = "Pod", pod_name, pod_ns

        key: WorkloadKey = (kind, wl_name, wl_ns)
        workload_restarts[key]["total_restarts"] += pod_restarts
        workload_restarts[key]["pod_details"].append({
            "pod": pod_name,
            "namespace": pod_ns,
            "restarts": pod_restarts,
            "last_restart": pod_last_restart,
        })
        if pod_last_restart and pod_last_restart > workload_restarts[key]["last_restart"]:
            workload_restarts[key]["last_restart"] = pod_last_restart

    results = []
    for (kind, name, ns_val), info in workload_restarts.items():
        total = info["total_restarts"]
        if total < min_restarts:
            continue
        pod_details = sorted(info["pod_details"], key=lambda p: p["restarts"], reverse=True)
        results.append({
            "kind": kind,
            "name": name,
            "namespace": ns_val,
            "total_restarts": total,
            "pod_count": len(pod_details),
            "last_restart": info["last_restart"],
            "top_pods": pod_details[:5],
        })

    results.sort(key=lambda r: r["total_restarts"], reverse=True)

    total_restarts = sum(r["total_restarts"] for r in results)
    workloads_with_restarts = sum(1 for r in results if r["total_restarts"] > 0)

    return {
        "workloads": results,
        "summary": {
            "total_workloads": len(results),
            "workloads_with_restarts": workloads_with_restarts,
            "total_restarts": total_restarts,
        },
    }


# ── Loop 57: PDB Coverage Analyzer ───────────────────────────────────────────

@app.get("/clusters/{cluster_id:path}/pdb-coverage")
async def pdb_coverage(cluster_id: str, namespace: str = "", min_replicas: int = 1):
    """Map deployments and statefulsets against PodDisruptionBudgets; surface uncovered workloads."""
    client = await _cluster(cluster_id)
    ns = namespace or None

    deps_r, sts_r, pdbs_r = await asyncio.gather(
        kube_list(client, "deployments", ns),
        kube_list(client, "statefulsets", ns),
        kube_list(client, "poddisruptionbudgets", ns),
        return_exceptions=True,
    )
    def _safe(v): return v if not isinstance(v, Exception) else []
    deployments = _safe(deps_r)
    statefulsets = _safe(sts_r)
    pdbs        = _safe(pdbs_r)

    # Index PDBs by (namespace, selector) to match against workloads
    # PDB has spec.selector.matchLabels; match workloads whose pods share those labels
    pdb_index: list[dict] = []
    for pdb in pdbs:
        meta = pdb.get("metadata", {})
        spec = pdb.get("spec", {})
        status = pdb.get("status", {})
        sel = spec.get("selector", {}).get("matchLabels", {})
        min_avail = spec.get("minAvailable")   # int or "50%"
        max_unavail = spec.get("maxUnavailable")
        pdb_index.append({
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "selector": sel,
            "min_available": min_avail,
            "max_unavailable": max_unavail,
            "current_healthy": status.get("currentHealthy", 0),
            "disruptions_allowed": status.get("disruptionsAllowed", 0),
        })

    def _find_pdb(wl_ns: str, wl_labels: dict) -> dict | None:
        for p in pdb_index:
            if p["namespace"] != wl_ns:
                continue
            if p["selector"] and all(wl_labels.get(k) == v for k, v in p["selector"].items()):
                return p
        return None

    def _analyze_workload(wl: dict, kind: str) -> dict:
        meta = wl.get("metadata", {})
        spec = wl.get("spec", {})
        status = wl.get("status", {})
        wl_ns   = meta.get("namespace", "")
        wl_name = meta.get("name", "")
        desired = spec.get("replicas", 1) or 1
        ready   = status.get("readyReplicas", 0) or 0
        pod_labels = spec.get("template", {}).get("metadata", {}).get("labels", {})

        pdb = _find_pdb(wl_ns, pod_labels)
        covered = pdb is not None

        # Assess PDB quality if covered
        pdb_quality = None
        if covered and pdb:
            min_av = pdb["min_available"]
            max_un = pdb["max_unavailable"]
            if isinstance(min_av, int) and min_av >= desired:
                pdb_quality = "misconfigured"  # min_available >= replicas prevents all disruption
            elif max_un == 0 or min_av == desired:
                pdb_quality = "too_strict"     # no disruption ever allowed
            else:
                pdb_quality = "ok"

        issues: list[str] = []
        if not covered and desired >= min_replicas:
            issues.append("no_pdb")
        if pdb_quality == "misconfigured":
            issues.append("pdb_misconfigured")
        if pdb_quality == "too_strict":
            issues.append("pdb_too_strict")

        return {
            "kind": kind,
            "name": wl_name,
            "namespace": wl_ns,
            "replicas": desired,
            "ready": ready,
            "covered": covered,
            "pdb_name": pdb["name"] if pdb else None,
            "pdb_min_available": pdb["min_available"] if pdb else None,
            "pdb_max_unavailable": pdb["max_unavailable"] if pdb else None,
            "pdb_quality": pdb_quality,
            "issues": issues,
        }

    results: list[dict] = []
    for dep in deployments:
        desired = (dep.get("spec", {}).get("replicas") or 0)
        if desired < min_replicas:
            continue
        results.append(_analyze_workload(dep, "Deployment"))

    for sts in statefulsets:
        desired = (sts.get("spec", {}).get("replicas") or 0)
        if desired < min_replicas:
            continue
        results.append(_analyze_workload(sts, "StatefulSet"))

    # Sort: uncovered multi-replica first, then by name
    results.sort(key=lambda r: (0 if r["issues"] else 1, -r["replicas"], r["name"]))

    uncovered = sum(1 for r in results if not r["covered"])
    covered_count = len(results) - uncovered
    misconfigured = sum(1 for r in results if r["pdb_quality"] == "misconfigured")

    return {
        "workloads": results,
        "pdbs": pdb_index,
        "summary": {
            "total_workloads": len(results),
            "covered": covered_count,
            "uncovered": uncovered,
            "misconfigured_pdbs": misconfigured,
            "total_pdbs": len(pdbs),
        },
    }


# ── Loop 58: Pod Anti-Affinity Coverage ──────────────────────────────────────

@app.get("/clusters/{cluster_id:path}/affinity-coverage")
async def affinity_coverage(cluster_id: str, namespace: str = "", min_replicas: int = 2):
    """Check multi-replica workloads for missing podAntiAffinity or topologySpreadConstraints."""
    client = await _cluster(cluster_id)
    ns = namespace or None

    deps_r, sts_r = await asyncio.gather(
        kube_list(client, "deployments", ns),
        kube_list(client, "statefulsets", ns),
        return_exceptions=True,
    )
    def _safe(v): return v if not isinstance(v, Exception) else []
    deployments = _safe(deps_r)
    statefulsets = _safe(sts_r)

    def _analyze(wl: dict, kind: str) -> dict | None:
        meta = wl.get("metadata", {})
        spec = wl.get("spec", {})
        status = wl.get("status", {})
        replicas = spec.get("replicas") or 0
        if replicas < min_replicas:
            return None
        ready = status.get("readyReplicas", 0) or 0
        pod_spec = spec.get("template", {}).get("spec", {})
        affinity = pod_spec.get("affinity", {})
        anti = affinity.get("podAntiAffinity", {})
        tsc = pod_spec.get("topologySpreadConstraints", [])

        has_required_anti = bool(anti.get("requiredDuringSchedulingIgnoredDuringExecution"))
        has_preferred_anti = bool(anti.get("preferredDuringSchedulingIgnoredDuringExecution"))
        has_tsc = bool(tsc)
        has_any = has_required_anti or has_preferred_anti or has_tsc

        if has_required_anti or (has_tsc and any(c.get("whenUnsatisfiable") == "DoNotSchedule" for c in tsc)):
            protection = "required"
        elif has_preferred_anti or has_tsc:
            protection = "preferred"
        else:
            protection = "none"

        issues: list[str] = []
        if not has_any:
            issues.append("no_anti_affinity")
        if replicas > 1 and not has_any:
            pass  # already captured

        return {
            "kind": kind,
            "name": meta.get("name", ""),
            "namespace": meta.get("namespace", ""),
            "replicas": replicas,
            "ready": ready,
            "protection": protection,
            "has_anti_affinity": bool(anti),
            "has_tsc": has_tsc,
            "tsc_count": len(tsc),
            "required_anti_affinity": has_required_anti,
            "preferred_anti_affinity": has_preferred_anti,
            "issues": issues,
        }

    results: list[dict] = []
    for dep in deployments:
        r = _analyze(dep, "Deployment")
        if r:
            results.append(r)
    for sts in statefulsets:
        r = _analyze(sts, "StatefulSet")
        if r:
            results.append(r)

    # Sort: unprotected first, then by replicas desc
    results.sort(key=lambda r: (0 if r["protection"] == "none" else 1, -r["replicas"], r["name"]))

    unprotected = sum(1 for r in results if r["protection"] == "none")
    preferred_only = sum(1 for r in results if r["protection"] == "preferred")
    fully_protected = sum(1 for r in results if r["protection"] == "required")

    return {
        "workloads": results,
        "summary": {
            "total_workloads": len(results),
            "unprotected": unprotected,
            "preferred_only": preferred_only,
            "fully_protected": fully_protected,
        },
    }


# ── Loop 59: Pod Security Context Audit ──────────────────────────────────────

def _parse_uid(run_as_user) -> int | None:
    if run_as_user is None:
        return None
    try:
        return int(run_as_user)
    except (ValueError, TypeError):
        return None


@app.get("/clusters/{cluster_id:path}/security-audit")
async def security_audit(cluster_id: str, namespace: str = ""):
    """Scan pods for security context risks: privileged, root, hostNetwork, escalation."""
    client = await _cluster(cluster_id)
    ns = namespace or None
    pods = await kube_list(client, "pods", ns)

    findings: list[dict] = []
    summary = {
        "total_pods": 0, "flagged_pods": 0,
        "privileged": 0, "run_as_root": 0, "allow_escalation": 0,
        "host_network": 0, "host_pid": 0, "host_ipc": 0,
        "no_read_only_root": 0,
    }

    for pod in pods:
        meta  = pod.get("metadata", {})
        spec  = pod.get("spec", {})
        phase = pod.get("status", {}).get("phase", "")

        if phase in ("Succeeded", "Failed", "Unknown"):
            continue

        summary["total_pods"] += 1
        pod_sc = spec.get("securityContext", {}) or {}
        host_network = bool(spec.get("hostNetwork"))
        host_pid     = bool(spec.get("hostPID"))
        host_ipc     = bool(spec.get("hostIPC"))

        pod_risks: list[str] = []
        if host_network:
            pod_risks.append("host_network")
            summary["host_network"] += 1
        if host_pid:
            pod_risks.append("host_pid")
            summary["host_pid"] += 1
        if host_ipc:
            pod_risks.append("host_ipc")
            summary["host_ipc"] += 1

        container_details: list[dict] = []
        containers = spec.get("containers", []) + spec.get("initContainers", [])
        for c in containers:
            csc = c.get("securityContext", {}) or {}
            c_risks: list[str] = []

            privileged = bool(csc.get("privileged"))
            allow_esc  = csc.get("allowPrivilegeEscalation")
            read_only  = csc.get("readOnlyRootFilesystem")
            run_as_user = csc.get("runAsUser") or pod_sc.get("runAsUser")
            run_as_non_root = csc.get("runAsNonRoot") or pod_sc.get("runAsNonRoot")

            uid = _parse_uid(run_as_user)
            is_root = (uid == 0) or (uid is None and run_as_non_root is not True)

            if privileged:
                c_risks.append("privileged")
                summary["privileged"] += 1
            if allow_esc is True or (allow_esc is None and privileged is False):
                c_risks.append("allow_escalation")
                summary["allow_escalation"] += 1
            if is_root:
                c_risks.append("run_as_root")
                summary["run_as_root"] += 1
            if not read_only:
                c_risks.append("no_read_only_root_fs")
                summary["no_read_only_root"] += 1

            if c_risks:
                container_details.append({
                    "container": c.get("name", ""),
                    "image": c.get("image", ""),
                    "privileged": privileged,
                    "allow_privilege_escalation": allow_esc,
                    "run_as_user": run_as_user,
                    "run_as_non_root": run_as_non_root,
                    "read_only_root_fs": bool(read_only),
                    "risks": c_risks,
                })

        all_risks = pod_risks + [r for cd in container_details for r in cd["risks"]]
        if all_risks:
            summary["flagged_pods"] += 1
            findings.append({
                "name": meta.get("name", ""),
                "namespace": meta.get("namespace", ""),
                "phase": phase,
                "host_network": host_network,
                "host_pid": host_pid,
                "host_ipc": host_ipc,
                "containers": container_details,
                "risks": list(dict.fromkeys(all_risks)),  # deduplicated
                "risk_score": len(all_risks),
            })

    # Sort by risk_score desc
    findings.sort(key=lambda f: -f["risk_score"])

    return {"findings": findings, "summary": summary}


# ── Loop 60: Namespace Label Compliance ──────────────────────────────────────

_SYSTEM_NAMESPACES = frozenset({
    "kube-system", "kube-public", "kube-node-lease",
    "cert-manager", "monitoring", "ingress-nginx",
})

_PSA_LABELS = (
    "pod-security.kubernetes.io/enforce",
    "pod-security.kubernetes.io/warn",
    "pod-security.kubernetes.io/audit",
)
_RECOMMENDED_LABELS = (
    "app.kubernetes.io/managed-by",
    "environment",
    "team",
)


@app.get("/clusters/{cluster_id:path}/namespace-labels")
async def namespace_labels(
    cluster_id: str,
    include_system: bool = False,
    required: str = "",   # comma-separated custom required label keys
):
    """Check namespaces for missing PSA and recommended labels."""
    client = await _cluster(cluster_id)
    namespaces = await kube_list(client, "namespaces", None)

    custom_required = [k.strip() for k in required.split(",") if k.strip()] if required else []

    results: list[dict] = []
    summary = {
        "total": 0,
        "system_namespaces": 0,
        "no_psa_label": 0,
        "no_team_label": 0,
        "no_env_label": 0,
        "missing_custom": 0,
        "fully_labeled": 0,
    }

    for ns in namespaces:
        meta   = ns.get("metadata", {})
        name   = meta.get("name", "")
        labels = meta.get("labels", {}) or {}
        is_system = name in _SYSTEM_NAMESPACES or name.startswith("kube-")

        summary["total"] += 1
        if is_system:
            summary["system_namespaces"] += 1
            if not include_system:
                continue

        psa_mode = None
        for pl in _PSA_LABELS:
            if pl in labels:
                psa_mode = labels[pl]
                break

        has_team  = any(k in labels for k in ("team", "owner", "app.kubernetes.io/part-of"))
        has_env   = any(k in labels for k in ("environment", "env", "stage"))
        missing_custom = [k for k in custom_required if k not in labels]

        issues: list[str] = []
        if not psa_mode and not is_system:
            issues.append("no_psa_label")
            summary["no_psa_label"] += 1
        if not has_team:
            issues.append("no_team_label")
            summary["no_team_label"] += 1
        if not has_env:
            issues.append("no_env_label")
            summary["no_env_label"] += 1
        if missing_custom:
            issues.append("missing_custom_labels")
            summary["missing_custom"] += 1

        if not issues:
            summary["fully_labeled"] += 1

        results.append({
            "name": name,
            "is_system": is_system,
            "labels": labels,
            "label_count": len(labels),
            "psa_mode": psa_mode,
            "has_team_label": has_team,
            "has_env_label": has_env,
            "missing_custom_labels": missing_custom,
            "issues": issues,
        })

    # Sort: namespaces with most issues first, then alphabetically
    results.sort(key=lambda r: (-len(r["issues"]), r["name"]))

    return {"namespaces": results, "summary": summary}


# ── Floating AI assistant ─────────────────────────────────────────────────────

@app.get("/ask")
async def floating_ask(
    question: str = Query(...),
    section: str = Query(""),
    namespace: str = Query(""),
    cluster_id: str = Query(""),
):
    """Context-aware K8s/MCO assistant for the floating AI panel."""
    ctx_parts = []
    if cluster_id:
        ctx_parts.append(f"cluster: {cluster_id}")
    if namespace:
        ctx_parts.append(f"namespace: {namespace}")
    if section:
        ctx_parts.append(f"current section: {section}")
    ctx_str = " | ".join(ctx_parts) if ctx_parts else "general K8s context"

    system_prompt = (
        "You are a senior Kubernetes and VMware Cloud Foundation engineer helping an ICT admin "
        "who is migrating from OpenShift to MCO (Multi-Cluster Operations platform built on Kubernetes).\n"
        "Key context: OpenShift concepts map to K8s as follows — Projects→Namespaces, "
        "Routes→Ingresses, DeploymentConfigs→Deployments, ImageStreams→Container registries, "
        "BuildConfigs→CI/CD pipelines (not native K8s), SCC→PodSecurityAdmission.\n"
        "Always be concrete, practical, and when relevant explain the OpenShift equivalent.\n"
        "Use markdown formatting. Keep responses concise but complete.\n"
        f"Current user context: {ctx_str}"
    )

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=60) as http:
                resp = await http.post(LLM_GATEWAY_URL + "/chat",
                                       json={"prompt": f"{system_prompt}\n\nQuestion: {question}"})
                if resp.status_code == 200:
                    text = resp.json().get("text", "")
                    yield f"data: {json.dumps({'text': text})}\n\n"
                else:
                    yield f"data: {json.dumps({'error': f'LLM returned {resp.status_code}'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
