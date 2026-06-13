"""Tests for Fleet Diff endpoint (Loop 55)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _node(name, cpu="4", mem="8Gi", ready=True):
    status = "True" if ready else "False"
    return {
        "metadata": {"name": name},
        "status": {
            "conditions": [{"type": "Ready", "status": status}],
            "allocatable": {"cpu": cpu, "memory": mem},
        },
    }


def _pod(name, ns="default", phase="Running"):
    return {"metadata": {"name": name, "namespace": ns}, "status": {"phase": phase}}


def _dep(name, ns="default", replicas=1, ready=1):
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {"replicas": replicas},
        "status": {"readyReplicas": ready},
    }


def _pvc(name, ns="default", phase="Bound", cap="10Gi"):
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {"resources": {"requests": {"storage": cap}}},
        "status": {"phase": phase, "capacity": {"storage": cap}},
    }


def _ns(name):
    return {"metadata": {"name": name}}


def _secret(name):
    return {"metadata": {"name": name}, "type": "Opaque", "data": {}}


def _setup_cluster(mock_list, mock_cluster_fn, cluster_id, nodes, pods, deps, pvcs, namespaces, secrets=None):
    mock_cluster_fn.side_effect = AsyncMock(return_value=MagicMock())
    data = {
        "nodes": nodes, "pods": pods, "deployments": deps,
        "statefulsets": [], "pvcs": pvcs, "namespaces": namespaces,
        "secrets": secrets or [],
    }
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))


# ── Missing query params → 400 ─────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_missing_params_400(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[])
    resp = client.get("/fleet/diff?a=cluster-a")
    assert resp.status_code in (400, 422)


# ── Both clusters reachable ───────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_both_reachable(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    data = {
        "nodes": [_node("w1")], "pods": [_pod("app")], "deployments": [_dep("api")],
        "statefulsets": [], "pvcs": [_pvc("db")], "namespaces": [_ns("app-ns")],
        "secrets": [_secret("s1")],
    }
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))
    body = client.get("/fleet/diff?a=ns/a&b=ns/b").json()
    assert body["cluster_a"]["reachable"] is True
    assert body["cluster_b"]["reachable"] is True
    assert isinstance(body["diff"], list)
    assert len(body["diff"]) > 0


# ── Diff rows contain delta ────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_diff_delta_computed(mock_list, mock_cluster):
    call_count = [0]

    def make_client(*a, **kw):
        call_count[0] += 1
        return MagicMock()

    mock_cluster.side_effect = AsyncMock(side_effect=make_client)

    invocation = [0]
    def list_side(c, kind, *a, **kw):
        invocation[0] += 1
        # First cluster: 2 nodes; second cluster: 1 node
        if invocation[0] <= 7:
            data = {"nodes": [_node("w1"), _node("w2")], "pods": [], "deployments": [],
                    "statefulsets": [], "pvcs": [], "namespaces": [], "secrets": []}
        else:
            data = {"nodes": [_node("w1")], "pods": [], "deployments": [],
                    "statefulsets": [], "pvcs": [], "namespaces": [], "secrets": []}
        return data.get(kind, [])

    mock_list.side_effect = AsyncMock(side_effect=list_side)
    body = client.get("/fleet/diff?a=ns/a&b=ns/b").json()
    node_row = next(r for r in body["diff"] if r["key"] == "node_count")
    assert node_row["a"] == 2
    assert node_row["b"] == 1
    assert node_row["delta"] == -1


# ── Unreachable cluster shows reachable=False ─────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_unreachable_cluster(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(side_effect=Exception("Connection refused"))
    mock_list.side_effect = AsyncMock(return_value=[])
    body = client.get("/fleet/diff?a=ns/bad&b=ns/bad").json()
    assert body["cluster_a"]["reachable"] is False
    assert body["cluster_b"]["reachable"] is False
    assert "error" in body["cluster_a"]


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_response_structure(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    data = {
        "nodes": [_node("w1")], "pods": [_pod("a"), _pod("b", phase="Pending")],
        "deployments": [_dep("api", replicas=2, ready=1)],
        "statefulsets": [], "pvcs": [_pvc("db"), _pvc("backup", phase="Pending")],
        "namespaces": [_ns("app")], "secrets": [],
    }
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))
    body = client.get("/fleet/diff?a=ns/a&b=ns/b").json()
    for field in ["cluster_a", "cluster_b", "diff"]:
        assert field in body
    snap = body["cluster_a"]
    for key in ["cluster_id", "reachable", "node_count", "pod_total", "pod_running",
                "deployment_count", "deployment_degraded", "pvc_count", "storage_gib"]:
        assert key in snap, f"missing key: {key}"
    row = body["diff"][0]
    for key in ["metric", "key", "a", "b", "delta"]:
        assert key in row, f"missing diff key: {key}"


# ── Degraded deployments counted ──────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_degraded_deployment_counted(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    data = {
        "nodes": [], "pods": [],
        "deployments": [_dep("ok", replicas=2, ready=2), _dep("bad", replicas=2, ready=0)],
        "statefulsets": [], "pvcs": [], "namespaces": [], "secrets": [],
    }
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))
    body = client.get("/fleet/diff?a=ns/a&b=ns/b").json()
    assert body["cluster_a"]["deployment_degraded"] == 1
    assert body["cluster_a"]["deployment_ready"] == 1
