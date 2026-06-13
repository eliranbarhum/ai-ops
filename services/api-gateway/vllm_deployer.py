"""
Manages the on-prem Ollama Kubernetes deployment.
Uses in-cluster SA token directly via httpx (avoids kubernetes library SSL quirks on VKS).

Models are stored on the `vllm-model-cache` PersistentVolumeClaim (100 Gi, vcf-sp storage class)
so model files survive pod restarts — no re-download required after a pod recycle.
The container's ephemeral-storage is hard-capped at 4 Gi to prevent the prior
cluster-wide eviction cascade (image layers only, no model files).
"""

import asyncio
import json
import logging
import os
import ssl
import time
import httpx

logger = logging.getLogger("api-gateway.vllm")

NAMESPACE         = os.getenv("POD_NAMESPACE", "vcf-ai-ops")
DEPLOYMENT_NAME   = "vllm-server"
SERVICE_NAME      = "vllm-server"
PVC_NAME          = "vllm-model-cache"
VLLM_PORT         = 11434
VLLM_IMAGE        = "ollama/ollama:latest"

_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_SA_CA_PATH    = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
_K8S_HOST      = os.getenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
_K8S_PORT      = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS",
                            os.getenv("KUBERNETES_SERVICE_PORT", "443"))
_K8S_API       = f"https://{_K8S_HOST}:{_K8S_PORT}"

# Ollama tag → inference RAM needed (model file size + Ollama overhead)
_MODEL_RAM_GB: dict[str, int] = {
    "smollm2:1.7b":  4,
    "qwen2.5:7b":    8,
    "qwen2.5:14b":  16,
    "qwen2.5:32b":  32,
    "llama3.1:8b":   8,
    "llama3.1:70b": 48,
    "phi4:14b":     16,
    "mistral:7b":    8,
    "gemma3:9b":    12,
}

# Pull is considered stuck if it hasn't finished after this many seconds
_PULL_TIMEOUT_SECS = 7200   # 2 hours

# ---------------------------------------------------------------------------
# Pull state — shared in-process buffer; survives across HTTP requests
# ---------------------------------------------------------------------------
_pull_state: dict = {
    "status":       "idle",   # idle | deploying | pulling | ready | error
    "model":        "",
    "logs":         [],       # list[str] shown in the log console
    "progress_pct": 0,        # 0-100 derived from completed/total in Ollama NDJSON
    "started_at":   0.0,
}


def _log(line: str) -> None:
    _pull_state["logs"].append(line)
    logger.info(f"[pull] {line}")


def get_pull_logs() -> dict:
    return {
        "status":       _pull_state["status"],
        "model":        _pull_state["model"],
        "logs":         list(_pull_state["logs"]),
        "progress_pct": _pull_state["progress_pct"],
    }


def _is_stuck() -> bool:
    """Return True if a pull has been in-progress past the timeout."""
    if _pull_state["status"] not in ("deploying", "pulling"):
        return False
    elapsed = time.time() - _pull_state.get("started_at", 0)
    return elapsed > _PULL_TIMEOUT_SECS


# ---------------------------------------------------------------------------
# K8s helpers
# ---------------------------------------------------------------------------

def _k8s_client() -> httpx.AsyncClient:
    token = open(_SA_TOKEN_PATH).read().strip()
    ctx   = ssl.create_default_context(cafile=_SA_CA_PATH)
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        verify=ctx,
        timeout=15.0,
    )


def _cpu_limit(ram_gb: int) -> int:
    return min(32, max(8, ram_gb // 4))


def _ram_to_resources(ram_gb: int) -> dict:
    mem_lim = f"{ram_gb}Gi"
    mem_req = f"{max(1, ram_gb - 2)}Gi"
    cpu_req = min(4, max(1, ram_gb // 8))
    cpu_lim = _cpu_limit(ram_gb)
    return {
        "requests": {
            "cpu":               str(cpu_req),
            "memory":            mem_req,
            "ephemeral-storage": "100Mi",
        },
        "limits": {
            "cpu":               str(cpu_lim),
            "memory":            mem_lim,
            # Hard cap prevents model blobs from spilling into ephemeral storage
            # and causing the cluster-wide eviction that happened previously.
            "ephemeral-storage": "4Gi",
        },
    }


def _deployment_body(ollama_model: str, ram_gb: int) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind":       "Deployment",
        "metadata": {
            "name":      DEPLOYMENT_NAME,
            "namespace": NAMESPACE,
            "labels":    {"app": DEPLOYMENT_NAME, "app.kubernetes.io/part-of": "mco"},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": DEPLOYMENT_NAME}},
            # Recreate: ensures old pod releases the RWO PVC before new pod starts.
            "strategy": {"type": "Recreate"},
            "template": {
                "metadata": {"labels": {"app": DEPLOYMENT_NAME}},
                "spec": {
                    "terminationGracePeriodSeconds": 60,
                    "containers": [{
                        "name":            "ollama",
                        "image":           VLLM_IMAGE,
                        "imagePullPolicy": "IfNotPresent",
                        "ports":           [{"containerPort": VLLM_PORT, "name": "http"}],
                        "env": [
                            {"name": "OLLAMA_HOST",              "value": f"0.0.0.0:{VLLM_PORT}"},
                            {"name": "OLLAMA_MODELS",            "value": "/models"},
                            {"name": "OLLAMA_KEEP_ALIVE",        "value": "-1"},
                            {"name": "OLLAMA_NUM_PARALLEL",      "value": "1"},
                            {"name": "OLLAMA_MAX_LOADED_MODELS", "value": "1"},
                            {"name": "OLLAMA_FLASH_ATTENTION",   "value": "0"},
                            {"name": "OLLAMA_NUM_THREADS",       "value": str(_cpu_limit(ram_gb))},
                        ],
                        "resources": _ram_to_resources(ram_gb),
                        "volumeMounts": [{"name": "model-cache", "mountPath": "/models"}],
                        "readinessProbe": {
                            "httpGet":             {"path": "/api/tags", "port": VLLM_PORT},
                            "initialDelaySeconds": 20,
                            "periodSeconds":       10,
                            "failureThreshold":    18,
                        },
                        "livenessProbe": {
                            "httpGet":             {"path": "/", "port": VLLM_PORT},
                            "initialDelaySeconds": 60,
                            "periodSeconds":       30,
                            "failureThreshold":    3,
                        },
                    }],
                    "volumes": [{
                        "name": "model-cache",
                        "persistentVolumeClaim": {"claimName": PVC_NAME},
                    }],
                },
            },
        },
    }


def _service_body() -> dict:
    return {
        "apiVersion": "v1",
        "kind":       "Service",
        "metadata": {
            "name":      SERVICE_NAME,
            "namespace": NAMESPACE,
            "labels":    {"app": SERVICE_NAME, "app.kubernetes.io/part-of": "mco"},
        },
        "spec": {
            "selector": {"app": DEPLOYMENT_NAME},
            "ports":    [{"port": VLLM_PORT, "targetPort": VLLM_PORT, "name": "http"}],
            "type":     "ClusterIP",
        },
    }


async def _model_already_on_pvc(model: str) -> bool:
    """Return True if the model is already stored on the PVC (no pull needed)."""
    try:
        svc_url = f"http://{SERVICE_NAME}:{VLLM_PORT}"
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{svc_url}/api/tags")
            if r.status_code == 200:
                names = [m.get("name", "") for m in r.json().get("models", [])]
                return model in names
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Pull — streaming NDJSON from Ollama /api/pull
# ---------------------------------------------------------------------------

async def _pull_model_background(model: str) -> None:
    """
    Wait for Ollama pod readiness, then stream the model pull.
    Populates _pull_state["logs"] with human-readable lines and tracks progress %.
    """
    svc_url = f"http://{SERVICE_NAME}:{VLLM_PORT}"

    _pull_state["status"] = "deploying"
    for attempt in range(30):
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{svc_url}/")
                if r.status_code == 200:
                    break
        except Exception:
            pass
        await asyncio.sleep(10)
    else:
        _pull_state["status"] = "error"
        _log("✗ Timed out waiting for Ollama pod to become ready")
        return

    _log("Pod ready — starting model pull…")
    _pull_state["status"] = "pulling"
    _pull_state["progress_pct"] = 0

    try:
        last_pct_logged = -1

        async with httpx.AsyncClient(timeout=7200.0) as c:
            async with c.stream(
                "POST",
                f"{svc_url}/api/pull",
                json={"name": model, "stream": True},
            ) as resp:
                resp.raise_for_status()
                async for raw_line in resp.aiter_lines():
                    if not raw_line.strip():
                        continue
                    try:
                        obj = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    status    = obj.get("status", "")
                    total     = obj.get("total", 0)
                    completed = obj.get("completed", 0)

                    if total and completed:
                        pct = int(completed * 100 / total)
                        _pull_state["progress_pct"] = pct
                        if pct - last_pct_logged >= 10:
                            mb_done  = completed // (1024 * 1024)
                            mb_total = total     // (1024 * 1024)
                            bar_done = pct // 5
                            bar = "█" * bar_done + "░" * (20 - bar_done)
                            _log(f"{bar}  {pct}%  {mb_done} MB / {mb_total} MB")
                            last_pct_logged = pct
                    elif status and not total:
                        _log(status)

        _pull_state["status"]       = "ready"
        _pull_state["progress_pct"] = 100
        _log("✓ Model loaded — ready for inference")

    except Exception as e:
        _pull_state["status"] = "error"
        _log(f"✗ Pull failed: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def pull_model(model: str, ram_gb: int) -> dict:
    """
    Ensure Ollama is deployed with correct RAM for `model`, then pull it.
    If the model is already on the PVC, skips the download.
    Returns immediately; the pull runs in the background.
    """
    # Reject concurrent pulls unless the previous one is stuck/errored
    if _pull_state["status"] in ("deploying", "pulling") and not _is_stuck():
        return {"ok": False, "message": f"Already pulling {_pull_state['model']} — wait for it to finish"}

    if _is_stuck():
        _log(f"⚠ Previous pull of {_pull_state['model']} timed out — resetting")

    # Use default RAM if caller didn't specify a model-appropriate value
    if ram_gb <= 0:
        ram_gb = _MODEL_RAM_GB.get(model, 16)

    _pull_state.update({
        "status":       "deploying",
        "model":        model,
        "logs":         [],
        "progress_pct": 0,
        "started_at":   time.time(),
    })

    _log(f"Pulling {model} (RAM={ram_gb} GB, storage=PVC:{PVC_NAME})…")

    try:
        async with _k8s_client() as k8s:
            dep_url  = f"{_K8S_API}/apis/apps/v1/namespaces/{NAMESPACE}/deployments"
            dep_body = _deployment_body(model, ram_gb)

            r = await k8s.get(f"{dep_url}/{DEPLOYMENT_NAME}")
            if r.status_code == 404:
                r = await k8s.post(dep_url, content=json.dumps(dep_body))
                r.raise_for_status()
                _log("Created Ollama deployment (PVC-backed).")
            else:
                existing  = r.json()
                containers = (existing.get("spec", {}).get("template", {})
                              .get("spec", {}).get("containers", [{}]))
                existing_mem = (containers[0].get("resources", {})
                                .get("limits", {}).get("memory", ""))
                desired_mem  = f"{ram_gb}Gi"

                # Check that volumes use the PVC (not the old emptyDir)
                existing_vols = (existing.get("spec", {}).get("template", {})
                                 .get("spec", {}).get("volumes", []))
                uses_pvc = any(v.get("persistentVolumeClaim") for v in existing_vols)

                if existing_mem != desired_mem or not uses_pvc:
                    _log(f"Redeploying: RAM {existing_mem}→{desired_mem}, pvc={uses_pvc}→True")
                    r = await k8s.put(
                        f"{dep_url}/{DEPLOYMENT_NAME}",
                        content=json.dumps({
                            **dep_body,
                            "metadata": {
                                **dep_body["metadata"],
                                "resourceVersion": existing["metadata"]["resourceVersion"],
                            },
                        }),
                    )
                    r.raise_for_status()
                else:
                    _log(f"Pod running with {ram_gb} GB RAM on PVC — ready to pull.")

            svc_url = f"{_K8S_API}/api/v1/namespaces/{NAMESPACE}/services"
            r = await k8s.get(f"{svc_url}/{SERVICE_NAME}")
            if r.status_code == 404:
                r = await k8s.post(svc_url, content=json.dumps(_service_body()))
                r.raise_for_status()
                _log("Created vllm-server Service.")

    except Exception as e:
        _pull_state["status"] = "error"
        _log(f"✗ Deployment failed: {e}")
        return {"ok": False, "message": str(e)}

    asyncio.create_task(_pull_model_background(model))
    return {
        "ok":      True,
        "model":   model,
        "ram_gb":  ram_gb,
        "storage": f"PVC:{PVC_NAME}",
        "message": f"Pull started — {model} will be loaded from PVC (persists across restarts)",
    }


async def delete_model(model: str) -> dict:
    """Delete a specific model from the Ollama store (frees PVC space)."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.request(
                "DELETE",
                f"http://{SERVICE_NAME}:{VLLM_PORT}/api/delete",
                json={"name": model},
            )
            if r.status_code in (200, 404):
                # Reset pull state if it was for this model so a fresh pull can start
                if _pull_state.get("model") == model:
                    _pull_state.update({"status": "idle", "model": "", "logs": [],
                                        "progress_pct": 0})
                return {"ok": True, "message": f"Model {model} removed from PVC"}
            return {"ok": False, "message": f"Ollama returned HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def reset_pull_state() -> dict:
    """Force-clear a stuck pull state so a new pull can be started."""
    prev = _pull_state["status"]
    _pull_state.update({"status": "idle", "model": "", "logs": [],
                         "progress_pct": 0, "started_at": 0.0})
    return {"ok": True, "message": f"Pull state reset (was: {prev})"}


async def deploy_vllm(model: str, ram_gb: int) -> dict:
    """Deploy (or redeploy) the Ollama pod without pulling a model."""
    try:
        async with _k8s_client() as k8s:
            dep_url  = f"{_K8S_API}/apis/apps/v1/namespaces/{NAMESPACE}/deployments"
            dep_body = _deployment_body(model, ram_gb)
            r = await k8s.get(f"{dep_url}/{DEPLOYMENT_NAME}")
            if r.status_code == 404:
                r      = await k8s.post(dep_url, content=json.dumps(dep_body))
                action = "created"
            else:
                r = await k8s.put(
                    f"{dep_url}/{DEPLOYMENT_NAME}",
                    content=json.dumps({
                        **dep_body,
                        "metadata": {
                            **dep_body["metadata"],
                            "resourceVersion": r.json()["metadata"]["resourceVersion"],
                        },
                    }),
                )
                action = "updated"
            r.raise_for_status()

            svc_url = f"{_K8S_API}/api/v1/namespaces/{NAMESPACE}/services"
            r = await k8s.get(f"{svc_url}/{SERVICE_NAME}")
            if r.status_code == 404:
                r = await k8s.post(svc_url, content=json.dumps(_service_body()))
                r.raise_for_status()

        return {
            "ok":      True,
            "action":  action,
            "model":   model,
            "ram_gb":  ram_gb,
            "storage": f"PVC:{PVC_NAME}",
            "vllm_url": f"http://{SERVICE_NAME}:{VLLM_PORT}",
            "message": f"Ollama {action} (PVC-backed). Use 'Pull Model' to download a model.",
        }
    except Exception as e:
        logger.error(f"Ollama deploy failed: {e}")
        return {"ok": False, "message": str(e)}


async def get_vllm_status() -> dict:
    try:
        async with _k8s_client() as k8s:
            dep_url = f"{_K8S_API}/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{DEPLOYMENT_NAME}"
            r = await k8s.get(dep_url)
            if r.status_code == 404:
                return {"deployed": False, "status": "not_deployed", "message": "Ollama is not deployed"}
            r.raise_for_status()
            dep     = r.json()
            ready   = dep.get("status", {}).get("readyReplicas") or 0
            desired = dep.get("spec",   {}).get("replicas")      or 1

            pr = await k8s.get(
                f"{_K8S_API}/api/v1/namespaces/{NAMESPACE}/pods",
                params={"labelSelector": f"app={DEPLOYMENT_NAME}"},
            )
            pod_info = []
            for pod in (pr.json().get("items", []) if pr.status_code == 200 else []):
                phase    = pod.get("status", {}).get("phase", "Unknown")
                log_tail = ""
                try:
                    pod_name = pod["metadata"]["name"]
                    lr = await k8s.get(
                        f"{_K8S_API}/api/v1/namespaces/{NAMESPACE}/pods/{pod_name}/log",
                        params={"tailLines": 30},
                    )
                    if lr.status_code == 200:
                        log_tail = lr.text
                except Exception:
                    pass
                pod_info.append({
                    "name":     pod["metadata"]["name"],
                    "phase":    phase,
                    "log_tail": log_tail,
                })

        dep_status    = "deploying"
        loaded_models: list = []

        if ready >= desired:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    tags_r = await client.get(f"http://{SERVICE_NAME}:{VLLM_PORT}/api/tags")
                    if tags_r.status_code == 200:
                        loaded_models = [m.get("name", "") for m in tags_r.json().get("models", [])]
                        dep_status    = "ready" if loaded_models else "no_model"
                    else:
                        dep_status = "ready"
            except Exception:
                dep_status = "ready"

        return {
            "deployed":         True,
            "status":           dep_status,
            "loaded_models":    loaded_models,
            "ready_replicas":   ready,
            "desired_replicas": desired,
            "pods":             pod_info,
            "vllm_url":         f"http://{SERVICE_NAME}:{VLLM_PORT}",
            "storage":          f"PVC:{PVC_NAME}",
        }
    except Exception as e:
        logger.error(f"Ollama status failed: {e}")
        return {"deployed": False, "status": "error", "message": str(e)}


async def delete_vllm() -> dict:
    """Undeploy the Ollama pod. Model files remain on the PVC for the next deploy."""
    try:
        deleted = []
        async with _k8s_client() as k8s:
            for url in [
                f"{_K8S_API}/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{DEPLOYMENT_NAME}",
                f"{_K8S_API}/api/v1/namespaces/{NAMESPACE}/services/{SERVICE_NAME}",
            ]:
                r = await k8s.delete(url)
                if r.status_code not in (200, 404):
                    r.raise_for_status()
                if r.status_code == 200:
                    deleted.append(url.split("/")[-1])
        _pull_state.update({"status": "idle", "model": "", "logs": [], "progress_pct": 0})
        return {"ok": True, "deleted": deleted,
                "message": "Ollama pod removed. Model files remain on PVC — no re-download needed on next deploy."}
    except Exception as e:
        logger.error(f"Ollama delete failed: {e}")
        return {"ok": False, "message": str(e)}
