"""Tests for node resource pressure dashboard (Loop 52)."""
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
        "metadata": {"name": name, "labels": {"node-role.kubernetes.io/worker": ""}},
        "status": {
            "conditions": [{"type": "Ready", "status": status}],
            "allocatable": {"cpu": cpu, "memory": mem},
            "nodeInfo": {"osImage": "Ubuntu 22.04", "kernelVersion": "5.15.0"},
        },
    }


def _pod(name, node, ns="default", req_cpu="100m", req_mem="128Mi", phase="Running"):
    return {
        "metadata": {"name": name, "namespace": ns},
        "status": {"phase": phase},
        "spec": {
            "nodeName": node,
            "containers": [{"name": "app", "resources": {
                "requests": {"cpu": req_cpu, "memory": req_mem},
                "limits":   {"cpu": req_cpu, "memory": req_mem},
            }}],
            "initContainers": [],
        },
    }


def _setup(mock_list, mock_metrics, nodes, pods, metrics_available=False, pod_metrics_data=None):
    data = {"nodes": nodes, "pods": pods}
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))
    mock_metrics.side_effect = AsyncMock(return_value={
        "available": metrics_available,
        "pods": pod_metrics_data or [],
    })


# ── Empty cluster ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_empty_cluster(mock_met, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup(mock_list, mock_met, nodes=[], pods=[])
    resp = client.get("/clusters/ns/cluster/nodes/pressure")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cluster"]["total_nodes"] == 0
    assert body["nodes"] == []


# ── Single idle node ──────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_idle_node_low_pressure(mock_met, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup(mock_list, mock_met,
        nodes=[_node("w1", cpu="4", mem="8Gi")],
        pods=[_pod("p1", "w1", req_cpu="100m", req_mem="128Mi")],
    )
    body = client.get("/clusters/ns/cluster/nodes/pressure").json()
    n = body["nodes"][0]
    assert n["name"] == "w1"
    assert n["pressure"] == "low"
    assert n["cpu_req_pct"] < 50
    assert n["mem_req_pct"] < 50


# ── High pressure node ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_high_pressure_node(mock_met, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    # 4 CPUs node, 3.5 CPU requested
    _setup(mock_list, mock_met,
        nodes=[_node("w1", cpu="4")],
        pods=[_pod("p1", "w1", req_cpu="3500m", req_mem="512Mi")],
    )
    body = client.get("/clusters/ns/cluster/nodes/pressure").json()
    n = body["nodes"][0]
    assert n["cpu_req_pct"] >= 80
    assert n["pressure"] in ("high", "over_committed")


# ── Over-committed node ───────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_over_committed_node(mock_met, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    # 4 CPU node, 5 CPU requested
    _setup(mock_list, mock_met,
        nodes=[_node("w1", cpu="4")],
        pods=[_pod("p1", "w1", req_cpu="5000m", req_mem="512Mi")],
    )
    body = client.get("/clusters/ns/cluster/nodes/pressure").json()
    n = body["nodes"][0]
    assert n["pressure"] == "over_committed"
    assert n["cpu_req_pct"] > 100
    assert body["cluster"]["over_committed"] == 1


# ── Not-ready node ────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_not_ready_node_pressure(mock_met, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup(mock_list, mock_met,
        nodes=[_node("w1", ready=False)],
        pods=[],
    )
    body = client.get("/clusters/ns/cluster/nodes/pressure").json()
    assert body["nodes"][0]["pressure"] == "not_ready"


# ── Over-committed node sorted first ─────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_over_committed_sorted_first(mock_met, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup(mock_list, mock_met,
        nodes=[_node("idle", cpu="4"), _node("heavy", cpu="4")],
        pods=[
            _pod("light", "idle", req_cpu="100m"),
            _pod("heavy-pod", "heavy", req_cpu="5000m"),
        ],
    )
    body = client.get("/clusters/ns/cluster/nodes/pressure").json()
    assert body["nodes"][0]["name"] == "heavy"  # over_committed sorts first


# ── Cluster summary aggregation ───────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_cluster_summary(mock_met, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup(mock_list, mock_met,
        nodes=[_node("w1", cpu="4"), _node("w2", cpu="4")],
        pods=[
            _pod("p1", "w1", req_cpu="1000m", req_mem="1Gi"),
            _pod("p2", "w2", req_cpu="2000m", req_mem="2Gi"),
        ],
    )
    body = client.get("/clusters/ns/cluster/nodes/pressure").json()
    c = body["cluster"]
    assert c["total_nodes"] == 2
    assert c["ready_nodes"] == 2
    assert c["total_req_cpu_m"] == pytest.approx(3000, abs=1)
    assert c["cluster_cpu_req_pct"] == pytest.approx(37.5, abs=1)


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_response_structure(mock_met, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup(mock_list, mock_met, nodes=[_node("w1")], pods=[_pod("p1", "w1")])
    body = client.get("/clusters/ns/cluster/nodes/pressure").json()
    assert "nodes" in body
    assert "cluster" in body
    assert "metrics_available" in body
    n = body["nodes"][0]
    for field in ["name", "ready", "pressure", "pod_count", "alloc_cpu_m", "alloc_mem_mib",
                  "req_cpu_m", "req_mem_mib", "cpu_req_pct", "mem_req_pct", "top_pods"]:
        assert field in n, f"missing field: {field}"
    c = body["cluster"]
    for field in ["total_nodes", "ready_nodes", "over_committed", "high_pressure",
                  "cluster_cpu_req_pct", "cluster_mem_req_pct"]:
        assert field in c, f"missing cluster field: {field}"


# ── Pending pods excluded from non-matching nodes ────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
@patch("main.pod_metrics")
def test_pending_pods_without_node_excluded(mock_met, mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    unscheduled = _pod("pending", "", phase="Pending")  # no nodeName
    unscheduled["spec"]["nodeName"] = ""
    _setup(mock_list, mock_met, nodes=[_node("w1")], pods=[unscheduled])
    body = client.get("/clusters/ns/cluster/nodes/pressure").json()
    assert body["nodes"][0]["pod_count"] == 0
