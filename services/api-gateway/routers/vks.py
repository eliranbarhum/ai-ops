import asyncio
import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from shared import _proxy

router = APIRouter()

VKS_BROKER_URL_DEFAULT = "http://vks-broker:8012"

import os
_VKS = os.getenv("VKS_BROKER_URL", VKS_BROKER_URL_DEFAULT)


async def _vks_stream(method: str, path: str, request: Request | None = None,
                      body: dict | None = None, params: dict | None = None):
    """Proxy a streaming (SSE) call to vks-broker."""
    headers = {}
    if request:
        for h in ("x-forwarded-user", "x-forwarded-email",
                  "x-forwarded-preferred-username", "x-request-id"):
            v = request.headers.get(h)
            if v:
                headers[h] = v

    async def _gen():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                method, f"{_VKS}{path}",
                json=body, params=params, headers=headers,
            ) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        yield f"{line}\n"
                    else:
                        yield "\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _fwd_headers(request: Request) -> dict:
    headers = {}
    for h in ("x-forwarded-user", "x-forwarded-email",
              "x-forwarded-preferred-username", "x-request-id"):
        v = request.headers.get(h)
        if v:
            headers[h] = v
    return headers


async def _vks_proxy(method: str, path: str, request: Request,
                     body: dict | None = None, params: dict | None = None,
                     timeout: float = 30.0) -> dict:
    headers = _fwd_headers(request)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                resp = await client.get(f"{_VKS}{path}", params=params, headers=headers)
            elif method == "POST":
                resp = await client.post(f"{_VKS}{path}", json=body or {}, params=params, headers=headers)
            elif method == "DELETE":
                resp = await client.delete(f"{_VKS}{path}", params=params, headers=headers)
            else:
                resp = await client.request(method, f"{_VKS}{path}", json=body, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        from fastapi import HTTPException
        import logging
        logging.getLogger("api-gateway.vks").warning(
            "vks-broker %s %s → %d: %.300s", method, path, e.response.status_code, e.response.text
        )
        try:
            detail = e.response.json().get("detail", "upstream error")
        except Exception:
            detail = "upstream error"
        raise HTTPException(status_code=e.response.status_code, detail=detail)
    except httpx.RequestError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"vks-broker unreachable: {e}")


# ── Cluster enumeration ───────────────────────────────────────────────────────

@router.get("/api/v1/vks/clusters")
async def vks_clusters(request: Request):
    return await _vks_proxy("GET", "/clusters", request)


@router.post("/api/v1/vks/clusters/import")
async def vks_import_cluster(request: Request):
    return await _vks_proxy("POST", "/clusters/import", request, body=await request.json())


@router.delete("/api/v1/vks/clusters/import/{name}")
async def vks_delete_imported(name: str, request: Request):
    return await _vks_proxy("DELETE", f"/clusters/import/{name}", request)


@router.get("/api/v1/vks/{cluster_id:path}/overview")
async def vks_overview(cluster_id: str, request: Request):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/overview", request)


@router.get("/api/v1/vks/{cluster_id:path}/namespaces")
async def vks_namespaces(cluster_id: str, request: Request):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/namespaces", request)


@router.post("/api/v1/vks/{cluster_id:path}/namespaces")
async def vks_create_namespace(cluster_id: str, request: Request):
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/namespaces", request, body=await request.json())


@router.delete("/api/v1/vks/{cluster_id:path}/namespaces/{name}")
async def vks_delete_namespace(cluster_id: str, name: str, request: Request, token: str = ""):
    params = {"token": token} if token else {}
    return await _vks_proxy("DELETE", f"/clusters/{cluster_id}/namespaces/{name}", request, params=params)


@router.get("/api/v1/vks/{cluster_id:path}/nodes")
async def vks_nodes(cluster_id: str, request: Request):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/nodes", request)


@router.get("/api/v1/vks/{cluster_id:path}/workloads")
async def vks_workloads(cluster_id: str, request: Request, namespace: str = "", kind: str = "deployments"):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/workloads", request,
                            params={"namespace": namespace, "kind": kind})


@router.post("/api/v1/vks/{cluster_id:path}/workloads")
async def vks_create_workload(cluster_id: str, request: Request):
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/workloads", request, body=await request.json())


@router.post("/api/v1/vks/{cluster_id:path}/workloads/{kind}/{name}/delete")
async def vks_delete_workload(cluster_id: str, kind: str, name: str, request: Request,
                               namespace: str = "", token: str = ""):
    params: dict = {}
    if namespace:
        params["namespace"] = namespace
    if token:
        params["token"] = token
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/workloads/{kind}/{name}/delete",
                             request, body={}, params=params)


@router.post("/api/v1/vks/{cluster_id:path}/workloads/{kind}/{name}/edit")
async def vks_edit_workload(cluster_id: str, kind: str, name: str, request: Request, namespace: str = ""):
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/workloads/{kind}/{name}/edit",
                             request, body=await request.json(), params={"namespace": namespace})


@router.get("/api/v1/vks/{cluster_id:path}/pods")
async def vks_pods(cluster_id: str, request: Request, namespace: str = ""):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/pods", request, params={"namespace": namespace})


@router.get("/api/v1/vks/{cluster_id:path}/pods/{pod}/logs")
async def vks_pod_logs(cluster_id: str, pod: str, request: Request,
                       namespace: str = "", container: str = "",
                       follow: bool = False, tail_lines: int = 200):
    return await _vks_stream(
        "GET", f"/clusters/{cluster_id}/pods/{pod}/logs",
        request=request,
        params={"namespace": namespace, "container": container,
                "follow": str(follow).lower(), "tail_lines": tail_lines},
    )


@router.post("/api/v1/vks/{cluster_id:path}/pods/{pod}/delete")
async def vks_delete_pod(cluster_id: str, pod: str, request: Request, namespace: str = "", token: str = ""):
    params: dict = {}
    if namespace:
        params["namespace"] = namespace
    if token:
        params["token"] = token
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/pods/{pod}/delete",
                             request, body={}, params=params)


@router.post("/api/v1/vks/{cluster_id:path}/pods/{pod}/diagnose")
async def vks_diagnose_pod(cluster_id: str, pod: str, request: Request, namespace: str = ""):
    return await _vks_stream("POST", f"/clusters/{cluster_id}/pods/{pod}/diagnose",
                             request=request, params={"namespace": namespace})


@router.get("/api/v1/vks/{cluster_id:path}/services")
async def vks_services(cluster_id: str, request: Request, namespace: str = ""):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/services", request, params={"namespace": namespace})


@router.get("/api/v1/vks/{cluster_id:path}/ingresses")
async def vks_ingresses(cluster_id: str, request: Request, namespace: str = ""):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/ingresses", request, params={"namespace": namespace})


@router.get("/api/v1/vks/{cluster_id:path}/networkpolicies")
async def vks_networkpolicies(cluster_id: str, request: Request, namespace: str = ""):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/networkpolicies", request,
                            params={"namespace": namespace})


@router.get("/api/v1/vks/{cluster_id:path}/pvcs")
async def vks_pvcs(cluster_id: str, request: Request, namespace: str = ""):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/pvcs", request, params={"namespace": namespace})


@router.get("/api/v1/vks/{cluster_id:path}/configmaps")
async def vks_configmaps(cluster_id: str, request: Request, namespace: str = ""):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/configmaps", request,
                            params={"namespace": namespace})


@router.post("/api/v1/vks/{cluster_id:path}/configmaps")
async def vks_create_configmap(cluster_id: str, request: Request):
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/configmaps", request, body=await request.json())


@router.get("/api/v1/vks/{cluster_id:path}/secrets")
async def vks_secrets(cluster_id: str, request: Request, namespace: str = ""):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/secrets", request, params={"namespace": namespace})


@router.post("/api/v1/vks/{cluster_id:path}/secrets/{name}/reveal")
async def vks_reveal_secret(cluster_id: str, name: str, request: Request,
                             namespace: str = "", token: str = ""):
    params: dict = {}
    if namespace:
        params["namespace"] = namespace
    if token:
        params["token"] = token
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/secrets/{name}/reveal",
                             request, body={}, params=params)


@router.get("/api/v1/vks/{cluster_id:path}/events")
async def vks_events(cluster_id: str, request: Request, namespace: str = "", severity: str = ""):
    return await _vks_proxy("GET", f"/clusters/{cluster_id}/events", request,
                            params={"namespace": namespace, "severity": severity})


@router.get("/api/v1/vks/{cluster_id:path}/kubeconfig")
async def vks_kubeconfig(cluster_id: str, request: Request):
    headers = _fwd_headers(request)
    async def _gen():
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("GET", f"{_VKS}/clusters/{cluster_id}/kubeconfig",
                                     headers=headers) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk
    return StreamingResponse(
        _gen(), media_type="application/x-yaml",
        headers={"Content-Disposition": f"attachment; filename=kubeconfig.yaml"},
    )


@router.post("/api/v1/vks/{cluster_id:path}/nodes/{node}/cordon")
async def vks_cordon(cluster_id: str, node: str, request: Request, token: str = ""):
    params = {"token": token} if token else {}
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/nodes/{node}/cordon",
                             request, body={}, params=params)


@router.post("/api/v1/vks/{cluster_id:path}/nodes/{node}/uncordon")
async def vks_uncordon(cluster_id: str, node: str, request: Request):
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/nodes/{node}/uncordon", request, body={})


@router.post("/api/v1/vks/{cluster_id:path}/nodes/{node}/drain")
async def vks_drain(cluster_id: str, node: str, request: Request, token: str = ""):
    params = {"token": token} if token else {}
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/nodes/{node}/drain",
                             request, body={}, params=params, timeout=120.0)


@router.post("/api/v1/vks/{cluster_id:path}/apply")
async def vks_apply(cluster_id: str, request: Request):
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/apply", request, body=await request.json())


@router.post("/api/v1/vks/{cluster_id:path}/deployments/{name}/scale")
async def vks_scale(cluster_id: str, name: str, request: Request, namespace: str = "", token: str = ""):
    params: dict = {}
    if namespace:
        params["namespace"] = namespace
    if token:
        params["token"] = token
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/deployments/{name}/scale",
                             request, body=await request.json(), params=params)


@router.post("/api/v1/vks/{cluster_id:path}/deployments/{name}/restart")
async def vks_restart(cluster_id: str, name: str, request: Request, namespace: str = "", token: str = ""):
    params: dict = {}
    if namespace:
        params["namespace"] = namespace
    if token:
        params["token"] = token
    return await _vks_proxy("POST", f"/clusters/{cluster_id}/deployments/{name}/restart",
                             request, body={}, params=params)


# ── AI endpoints ──────────────────────────────────────────────────────────────

@router.post("/api/v1/vks/generate/manifest")
async def vks_generate_manifest(request: Request):
    return await _vks_stream("POST", "/generate/manifest", request=request, body=await request.json())


@router.post("/api/v1/vks/nl/action")
async def vks_nl_action(request: Request):
    return await _vks_proxy("POST", "/nl/action", request, body=await request.json())


# ── Pod exec WebSocket proxy ──────────────────────────────────────────────────

@router.websocket("/api/v1/vks/{cluster_id:path}/pods/{pod}/exec")
async def vks_pod_exec_ws(
    websocket: WebSocket,
    cluster_id: str,
    pod: str,
    namespace: str = "default",
    command: str = "/bin/sh",
    container: str = "",
):
    """Transparent WebSocket proxy for pod exec (browser ↔ api-gateway ↔ vks-broker)."""
    import websockets as _ws
    from urllib.parse import urlencode, quote as _q
    await websocket.accept()
    qs_params: dict = {"namespace": namespace, "command": command}
    if container:
        qs_params["container"] = container
    broker_ws_url = (
        f"{_VKS.replace('http://', 'ws://').replace('https://', 'wss://')}"
        f"/clusters/{_q(cluster_id, safe='/')}/pods/{_q(pod, safe='')}/exec"
        f"?{urlencode(qs_params)}"
    )
    try:
        async with _ws.connect(broker_ws_url, max_size=2**20, open_timeout=15) as broker_ws:
            async def client_to_broker():
                try:
                    async for msg in websocket.iter_bytes():
                        await broker_ws.send(msg)
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            async def broker_to_client():
                try:
                    async for msg in broker_ws:
                        data = msg if isinstance(msg, bytes) else msg.encode()
                        await websocket.send_bytes(data)
                except Exception:
                    pass

            await asyncio.gather(client_to_broker(), broker_to_client())
    except Exception as e:
        try:
            await websocket.send_text(f"\r\n[error] {e}\r\n")
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ── Catch-all HTTP proxy for any new vks-broker endpoints ────────────────────
# Must come AFTER all specific routes so it doesn't shadow them.

# Broker root-level prefixes that are NOT cluster-scoped.
# Everything else arriving at the catch-all is a cluster-scoped path of the form
# "{namespace}/{cluster-name}/..." and must be forwarded as "/clusters/{path}".
_BROKER_ROOT_PREFIXES = ("fleet/", "audit", "health", "generate/", "nl/")


@router.api_route(
    "/api/v1/vks/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    include_in_schema=False,
)
async def vks_catch_all(path: str, request: Request):
    """Forward any unmatched vks request directly to vks-broker.

    Cluster-scoped paths arrive as "{namespace}/{cluster}/..." and must be
    translated to "/clusters/{namespace}/{cluster}/..." on the broker.
    Root-level paths (fleet, audit, health, …) are forwarded as-is.
    """
    headers = _fwd_headers(request)
    qs = str(request.url.query)
    if path.startswith(_BROKER_ROOT_PREFIXES):
        broker_path = f"/{path}"
    else:
        broker_path = f"/clusters/{path}"
    url = f"{_VKS}{broker_path}" + (f"?{qs}" if qs else "")
    body = None
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body = await request.body()
        except Exception:
            body = b""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.request(
                request.method, url,
                headers={**headers, "content-type": request.headers.get("content-type", "application/json")},
                content=body,
            )
        from fastapi.responses import Response
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type"),
        )
    except httpx.RequestError as e:
        from fastapi import HTTPException
        raise HTTPException(503, f"vks-broker unreachable: {e}")
