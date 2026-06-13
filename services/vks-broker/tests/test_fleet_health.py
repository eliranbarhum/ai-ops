"""Tests for multi-cluster fleet health endpoint (Loop 49)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _node(name, ready=True, cpu="4", mem="8Gi"):
    status = "True" if ready else "False"
    return {
        "metadata": {"name": name},
        "status": {
            "conditions": [{"type": "Ready", "status": status}],
            "allocatable": {"cpu": cpu, "memory": mem},
        },
    }


def _deploy(replicas=2, ready=2):
    return {"spec": {"replicas": replicas}, "status": {"readyReplicas": ready}}


def _ns(name):
    return {"metadata": {"name": name}}


def _mock_client(nodes=None, pods=None, deploys=None, namespaces=None, version=None):
    mock_http = MagicMock()
    version_resp = MagicMock()
    version_resp.status_code = 200
    version_resp.json.return_value = version or {"major": "1", "minor": "29"}
    mock_http.get = AsyncMock(return_value=version_resp)
    return mock_http


# ── No clusters ───────────────────────────────────────────────────────────────

@patch("main.list_clusters")
def test_fleet_no_clusters(mock_list):
    mock_list.return_value = {"clusters": []}
    resp = client.get("/fleet/k8s-health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["healthy"] == 0
    assert body["degraded"] == 0
    assert body["unreachable"] == 0


# ── Single healthy cluster ────────────────────────────────────────────────────

@patch("main.list_clusters")
@patch("main._cluster")
@patch("main.kube_list")
def test_fleet_healthy_cluster(mock_kube, mock_cluster, mock_list_clusters):
    mock_list_clusters.return_value = {"clusters": [
        {"cluster_id": "ns/cl-1", "name": "prod", "provider": "vsphere"},
    ]}
    mock_http = MagicMock()
    ver_resp = MagicMock(); ver_resp.status_code = 200; ver_resp.json.return_value = {"major": "1", "minor": "29"}
    mock_http.get = AsyncMock(return_value=ver_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    mock_kube.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [_node("w1"), _node("w2")],
        "pods": [{"metadata": {}, "status": {}}],
        "deployments": [_deploy(2, 2)],
        "statefulsets": [],
        "namespaces": [_ns("default"), _ns("prod")],
    }.get(kind, []))

    resp = client.get("/fleet/k8s-health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["healthy"] == 1
    assert body["clusters"][0]["name"] == "prod"
    assert body["clusters"][0]["status"] == "healthy"
    assert body["clusters"][0]["nodes"]["total"] == 2
    assert body["clusters"][0]["nodes"]["ready"] == 2
    assert body["clusters"][0]["namespaces"] == 2


# ── Degraded cluster (not all nodes ready) ───────────────────────────────────

@patch("main.list_clusters")
@patch("main._cluster")
@patch("main.kube_list")
def test_fleet_degraded_cluster(mock_kube, mock_cluster, mock_list_clusters):
    mock_list_clusters.return_value = {"clusters": [
        {"cluster_id": "ns/cl-2", "name": "staging", "provider": "vsphere"},
    ]}
    mock_http = MagicMock()
    ver_resp = MagicMock(); ver_resp.status_code = 200; ver_resp.json.return_value = {"major": "1", "minor": "28"}
    mock_http.get = AsyncMock(return_value=ver_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    mock_kube.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [_node("w1", ready=True), _node("w2", ready=False)],
        "pods": [],
        "deployments": [],
        "statefulsets": [],
        "namespaces": [],
    }.get(kind, []))

    resp = client.get("/fleet/k8s-health")
    body = resp.json()
    assert body["clusters"][0]["status"] == "degraded"
    assert body["degraded"] == 1


# ── Unreachable cluster ───────────────────────────────────────────────────────

@patch("main.list_clusters")
@patch("main._cluster")
def test_fleet_unreachable_cluster(mock_cluster, mock_list_clusters):
    mock_list_clusters.return_value = {"clusters": [
        {"cluster_id": "ns/dead", "name": "dead-cluster", "provider": "vsphere"},
    ]}
    mock_cluster.side_effect = AsyncMock(side_effect=Exception("connection refused"))
    resp = client.get("/fleet/k8s-health")
    body = resp.json()
    assert body["unreachable"] == 1
    assert body["clusters"][0]["status"] == "unreachable"
    assert body["clusters"][0]["name"] == "dead-cluster"


# ── Response structure ────────────────────────────────────────────────────────

@patch("main.list_clusters")
def test_fleet_response_structure(mock_list_clusters):
    mock_list_clusters.return_value = {"clusters": []}
    resp = client.get("/fleet/k8s-health")
    body = resp.json()
    assert "clusters" in body
    assert "total" in body
    assert "healthy" in body
    assert "degraded" in body
    assert "unreachable" in body


# ── Multiple clusters ─────────────────────────────────────────────────────────

@patch("main.list_clusters")
@patch("main._cluster")
@patch("main.kube_list")
def test_fleet_multiple_clusters(mock_kube, mock_cluster, mock_list_clusters):
    mock_list_clusters.return_value = {"clusters": [
        {"cluster_id": "ns/c1", "name": "c1", "provider": "vsphere"},
        {"cluster_id": "ns/c2", "name": "c2", "provider": "vsphere"},
    ]}
    mock_http = MagicMock()
    ver_resp = MagicMock(); ver_resp.status_code = 200; ver_resp.json.return_value = {"major": "1", "minor": "29"}
    mock_http.get = AsyncMock(return_value=ver_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    mock_kube.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [_node("w1")], "pods": [], "deployments": [], "statefulsets": [], "namespaces": [],
    }.get(kind, []))

    resp = client.get("/fleet/k8s-health")
    body = resp.json()
    assert body["total"] == 2
    names = {c["name"] for c in body["clusters"]}
    assert "c1" in names and "c2" in names
