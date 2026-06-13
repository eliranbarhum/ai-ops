"""Tests for cost-estimate endpoint (Loop 44)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _pod(name: str, ns: str, cpu_req: str, mem_req: str, phase: str = "Running") -> dict:
    return {
        "metadata": {"name": name, "namespace": ns},
        "status": {"phase": phase},
        "spec": {
            "containers": [{
                "name": "app",
                "resources": {"requests": {"cpu": cpu_req, "memory": mem_req}},
            }]
        },
    }


# ── Structure ─────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_cost_estimate_structure(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_pod("p1", "default", "500m", "512Mi")])
    resp = client.get("/clusters/ns/cluster/cost-estimate")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "namespaces" in body
    assert "top_pods" in body
    assert "pricing" in body
    t = body["total"]
    assert "hourly" in t and "monthly" in t and "cpu_cores" in t and "mem_gib" in t


# ── Calculations ──────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_cost_estimate_math(mock_list, mock_cluster):
    """1 vCPU + 1 GiB at default pricing = (0.048 + 0.006) = $0.054/hr."""
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_pod("p1", "default", "1000m", "1Gi")])
    resp = client.get("/clusters/ns/cluster/cost-estimate")
    body = resp.json()
    assert abs(body["total"]["hourly"] - 0.054) < 0.001
    assert abs(body["total"]["monthly"] - 0.054 * 730) < 0.1


@patch("main._cluster")
@patch("main.kube_list")
def test_cost_estimate_custom_pricing(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_pod("p1", "default", "1000m", "1Gi")])
    resp = client.get("/clusters/ns/cluster/cost-estimate?cpu_hour=0.1&mem_hour=0.01")
    body = resp.json()
    assert abs(body["total"]["hourly"] - 0.11) < 0.001
    assert body["pricing"]["cpu_hour"] == 0.1
    assert body["pricing"]["mem_hour"] == 0.01


# ── Only running pods ─────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_cost_estimate_skips_non_running(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("running", "default", "500m", "512Mi", phase="Running"),
        _pod("pending", "default", "500m", "512Mi", phase="Pending"),
        _pod("failed", "default", "500m", "512Mi", phase="Failed"),
    ])
    resp = client.get("/clusters/ns/cluster/cost-estimate")
    body = resp.json()
    assert body["total"]["cpu_cores"] == 0.5
    assert len(body["top_pods"]) == 1
    assert body["top_pods"][0]["name"] == "running"


# ── Namespace grouping ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_cost_estimate_groups_by_namespace(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("p1", "prod", "1000m", "1Gi"),
        _pod("p2", "prod", "1000m", "1Gi"),
        _pod("p3", "dev", "500m", "512Mi"),
    ])
    resp = client.get("/clusters/ns/cluster/cost-estimate")
    body = resp.json()
    ns_map = {n["namespace"]: n for n in body["namespaces"]}
    assert "prod" in ns_map
    assert "dev" in ns_map
    assert ns_map["prod"]["pod_count"] == 2
    assert ns_map["dev"]["pod_count"] == 1
    # prod costs more
    assert ns_map["prod"]["monthly"] > ns_map["dev"]["monthly"]


# ── Namespace ordering ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_cost_estimate_namespaces_sorted_by_cost(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod("cheap", "dev", "100m", "128Mi"),
        _pod("expensive", "prod", "4000m", "8Gi"),
    ])
    resp = client.get("/clusters/ns/cluster/cost-estimate")
    body = resp.json()
    assert body["namespaces"][0]["namespace"] == "prod"


# ── Empty cluster ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_cost_estimate_empty_cluster(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[])
    resp = client.get("/clusters/ns/cluster/cost-estimate")
    body = resp.json()
    assert body["total"]["monthly"] == 0.0
    assert body["namespaces"] == []
    assert body["top_pods"] == []


# ── Top pods limit ────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_cost_estimate_top_pods_capped_at_20(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[
        _pod(f"pod-{i}", "default", "100m", "128Mi") for i in range(30)
    ])
    resp = client.get("/clusters/ns/cluster/cost-estimate")
    assert len(resp.json()["top_pods"]) == 20
