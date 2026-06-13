import asyncio
import json as _json
import shlex
from typing import Literal
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from shared import LLM_GATEWAY_URL, CONFIG_STORE_URL
from routers.maintenance import is_in_maintenance_window

router = APIRouter()

_KUBECONFIG_SUPERVISOR = "/etc/kubectl/config"
_KUBECONFIG_WORKLOAD   = "/etc/kubectl-workload/config"

# Only these subcommands are permitted — everything else is blocked by default.
_ALLOWED_SUBCOMMANDS = frozenset({
    "get", "describe", "logs", "apply", "create", "delete",
    "rollout", "scale", "patch", "top", "events", "wait",
    "version", "explain", "diff",
})

# Flags that could override our kubeconfig or reach arbitrary servers.
_BLOCKED_FLAGS = frozenset({"--raw", "--kubeconfig", "--server", "--token", "--certificate-authority"})


def _kubeconfig_for_run(cluster: str) -> str:
    """Kubeconfig used to execute commands. Workload commands run inside the TKG cluster."""
    return _KUBECONFIG_WORKLOAD if cluster == "workload" else _KUBECONFIG_SUPERVISOR


def _kubeconfig_for_ns(cluster: str) -> str:
    """Kubeconfig used to list namespaces.
    Workload uses the supervisor so the picker shows tenant namespaces (vcf-ai-ops, cradu-prod…).
    """
    return _KUBECONFIG_SUPERVISOR


def _validate_and_build_args(cmd: str, cluster: str) -> list[str]:
    """Parse kubectl command, enforce denylist, inject kubeconfig. Raises HTTPException on violation."""
    try:
        args = shlex.split(cmd.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid command syntax: {e}")

    if not args or args[0] != "kubectl":
        raise HTTPException(status_code=400, detail="Command must start with kubectl")

    # Find the subcommand (first non-flag token after "kubectl")
    subcommand = next((a for a in args[1:] if not a.startswith("-")), "")
    if subcommand not in _ALLOWED_SUBCOMMANDS:
        raise HTTPException(status_code=403, detail=f"Subcommand '{subcommand}' is not permitted. Allowed: {', '.join(sorted(_ALLOWED_SUBCOMMANDS))}")

    for arg in args:
        flag = arg.split("=")[0]
        if flag in _BLOCKED_FLAGS:
            raise HTTPException(status_code=403, detail=f"Flag '{flag}' is not permitted")

    # Inject the managed kubeconfig as a proper argv element
    kc = _kubeconfig_for_run(cluster)
    if not any(a.startswith("--kubeconfig") for a in args):
        args = ["kubectl", f"--kubeconfig={kc}", *args[1:]]

    return args


class KubectlRunRequest(BaseModel):
    command: str
    cluster: Literal["supervisor", "workload"] = "supervisor"
    run_on_all: bool = False


@router.post("/api/v1/kubectl/generate")
async def kubectl_generate(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{LLM_GATEWAY_URL}/generate/kubectl", json=body)
        resp.raise_for_status()
        return resp.json()


_SYSTEM_NS_PREFIXES = ("kube-",)
_SYSTEM_NS_EXACT = {"default", "local-path-storage"}


@router.get("/api/v1/kubectl/namespaces")
async def kubectl_namespaces(cluster: Literal["supervisor", "workload"] = "supervisor"):
    kc = _kubeconfig_for_ns(cluster)
    proc = await asyncio.create_subprocess_exec(
        "kubectl", f"--kubeconfig={kc}", "get", "namespaces",
        "-o", "jsonpath={.items[*].metadata.name}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    all_ns = [n for n in stdout.decode().split() if n]
    if cluster == "workload":
        all_ns = [
            n for n in all_ns
            if n not in _SYSTEM_NS_EXACT
            and not any(n.startswith(p) for p in _SYSTEM_NS_PREFIXES)
        ]
    return {"namespaces": all_ns}


@router.post("/api/v1/kubectl/run")
async def kubectl_run(req: KubectlRunRequest):
    if not await is_in_maintenance_window():
        raise HTTPException(423, detail="kubectl execution is blocked outside a maintenance window. Configure one in Settings → Maintenance.")

    if req.run_on_all:
        return _broadcast_stream(req.command)

    args = _validate_and_build_args(req.command, req.cluster)

    async def _stream():
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip()
            yield f"data: {_json.dumps({'type': 'line', 'text': text})}\n\n"
        exit_code = await proc.wait()
        yield f"data: {_json.dumps({'type': 'done', 'exit_code': exit_code})}\n\n"

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _broadcast_stream(command: str) -> StreamingResponse:
    """Run command on supervisor and workload clusters in parallel, merge SSE streams."""

    async def _stream():
        q: asyncio.Queue = asyncio.Queue()

        async def _run_on(cluster: str) -> None:
            try:
                args = _validate_and_build_args(command, cluster)
            except HTTPException as e:
                await q.put({"type": "error", "cluster": cluster, "text": e.detail})
                await q.put({"type": "done",  "cluster": cluster, "exit_code": -1})
                return
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async for line in proc.stdout:
                await q.put({"type": "line", "cluster": cluster,
                             "text": line.decode("utf-8", errors="replace").rstrip()})
            exit_code = await proc.wait()
            await q.put({"type": "done", "cluster": cluster, "exit_code": exit_code})

        tasks = [
            asyncio.create_task(_run_on("supervisor")),
            asyncio.create_task(_run_on("workload")),
        ]
        done: set[str] = set()

        while len(done) < 2:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=300.0)
            except asyncio.TimeoutError:
                yield f"data: {_json.dumps({'type': 'error', 'cluster': 'all', 'text': 'broadcast timed out after 300s'})}\n\n"
                break
            yield f"data: {_json.dumps(ev)}\n\n"
            if ev.get("type") == "done":
                done.add(ev["cluster"])

        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/v1/kubectl/explain")
async def kubectl_explain(request: Request):
    body = await request.json()

    async def _proxy_stream():
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", f"{LLM_GATEWAY_URL}/generate/kubectl/explain",
                json=body, timeout=120.0,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        _proxy_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _get_user(request: Request) -> str:
    return (
        request.headers.get("x-forwarded-preferred-username")
        or request.headers.get("x-forwarded-user")
        or request.headers.get("x-forwarded-email")
        or "anonymous"
    )


@router.get("/api/v1/kubectl/pinned")
async def kubectl_list_pinned(request: Request):
    user = _get_user(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{CONFIG_STORE_URL}/kubectl/pinned", params={"user_id": user})
        resp.raise_for_status()
        return resp.json()


class PinnedCommandRequest(BaseModel):
    command: str
    label: str = ""


@router.post("/api/v1/kubectl/pinned")
async def kubectl_pin_command(req: PinnedCommandRequest, request: Request):
    user = _get_user(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{CONFIG_STORE_URL}/kubectl/pinned",
                                 json={"user_id": user, "command": req.command, "label": req.label})
        resp.raise_for_status()
        return resp.json()


@router.delete("/api/v1/kubectl/pinned/{cmd_id}")
async def kubectl_unpin_command(cmd_id: str, request: Request):
    user = _get_user(request)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(f"{CONFIG_STORE_URL}/kubectl/pinned/{cmd_id}",
                                   params={"user_id": user})
        if resp.status_code == 404:
            raise HTTPException(404, detail="Pinned command not found")
        resp.raise_for_status()
        return resp.json()
