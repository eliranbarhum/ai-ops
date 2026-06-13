"""Tests for GET /clusters/{id}/pdbs and /limitranges (Loop 33)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)

_PDB_HEALTHY = {
    "metadata": {"name": "my-pdb", "namespace": "default", "creationTimestamp": "2024-01-01T00:00:00Z"},
    "spec": {
        "selector": {"matchLabels": {"app": "my-app"}},
        "minAvailable": 2,
    },
    "status": {
        "currentHealthy": 3,
        "desiredHealthy": 2,
        "disruptionsAllowed": 1,
        "expectedPods": 3,
    },
}

_PDB_DEGRADED = {
    "metadata": {"name": "critical-pdb", "namespace": "default", "creationTimestamp": "2024-01-02T00:00:00Z"},
    "spec": {
        "selector": {"matchLabels": {"tier": "critical"}},
        "maxUnavailable": 0,
    },
    "status": {
        "currentHealthy": 1,
        "desiredHealthy": 3,
        "disruptionsAllowed": 0,
        "expectedPods": 3,
    },
}

_LIMITRANGE = {
    "metadata": {"name": "default-limits", "namespace": "default", "creationTimestamp": "2024-01-01T00:00:00Z"},
    "spec": {
        "limits": [
            {
                "type": "Container",
                "default": {"cpu": "500m", "memory": "256Mi"},
                "defaultRequest": {"cpu": "100m", "memory": "128Mi"},
                "max": {"cpu": "2", "memory": "1Gi"},
                "min": {"cpu": "50m", "memory": "64Mi"},
            }
        ]
    },
}

_LIMITRANGE_POD = {
    "metadata": {"name": "pod-limits", "namespace": "default", "creationTimestamp": "2024-01-03T00:00:00Z"},
    "spec": {
        "limits": [
            {
                "type": "Pod",
                "max": {"cpu": "4", "memory": "4Gi"},
                "min": {},
            }
        ]
    },
}


# ── PDB tests ─────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_pdbs_returns_list(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_PDB_HEALTHY, _PDB_DEGRADED])

    resp = client.get("/clusters/ns/cluster/pdbs?namespace=default")
    assert resp.status_code == 200
    body = resp.json()
    assert "pdbs" in body
    assert len(body["pdbs"]) == 2


@patch("main._cluster")
@patch("main.kube_list")
def test_pdbs_healthy_format(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_PDB_HEALTHY])

    resp = client.get("/clusters/ns/cluster/pdbs?namespace=default")
    pdb = resp.json()["pdbs"][0]
    assert pdb["name"] == "my-pdb"
    assert pdb["namespace"] == "default"
    assert pdb["min_available"] == 2
    assert pdb["max_unavailable"] is None
    assert pdb["current_healthy"] == 3
    assert pdb["desired_healthy"] == 2
    assert pdb["disruptions_allowed"] == 1
    assert pdb["expected_pods"] == 3
    assert pdb["selector"] == {"app": "my-app"}


@patch("main._cluster")
@patch("main.kube_list")
def test_pdbs_max_unavailable_format(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_PDB_DEGRADED])

    resp = client.get("/clusters/ns/cluster/pdbs?namespace=default")
    pdb = resp.json()["pdbs"][0]
    assert pdb["min_available"] is None
    assert pdb["max_unavailable"] == 0
    assert pdb["disruptions_allowed"] == 0
    assert pdb["current_healthy"] == 1
    assert pdb["desired_healthy"] == 3


@patch("main._cluster")
@patch("main.kube_list")
def test_pdbs_empty_namespace(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[])

    resp = client.get("/clusters/ns/cluster/pdbs")
    assert resp.status_code == 200
    assert resp.json()["pdbs"] == []


# ── LimitRange tests ──────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_limitranges_returns_list(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_LIMITRANGE, _LIMITRANGE_POD])

    resp = client.get("/clusters/ns/cluster/limitranges?namespace=default")
    assert resp.status_code == 200
    body = resp.json()
    assert "limitranges" in body
    assert len(body["limitranges"]) == 2


@patch("main._cluster")
@patch("main.kube_list")
def test_limitranges_container_format(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_LIMITRANGE])

    resp = client.get("/clusters/ns/cluster/limitranges?namespace=default")
    lr = resp.json()["limitranges"][0]
    assert lr["name"] == "default-limits"
    assert lr["namespace"] == "default"
    assert len(lr["limits"]) == 1
    lim = lr["limits"][0]
    assert lim["type"] == "Container"
    assert lim["default"]["cpu"] == "500m"
    assert lim["default"]["memory"] == "256Mi"
    assert lim["default_request"]["cpu"] == "100m"
    assert lim["max"]["cpu"] == "2"
    assert lim["min"]["cpu"] == "50m"


@patch("main._cluster")
@patch("main.kube_list")
def test_limitranges_pod_type(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_LIMITRANGE_POD])

    resp = client.get("/clusters/ns/cluster/limitranges?namespace=default")
    lr = resp.json()["limitranges"][0]
    lim = lr["limits"][0]
    assert lim["type"] == "Pod"
    assert lim["max"]["cpu"] == "4"
    assert lim["default"] == {}
    assert lim["default_request"] == {}


@patch("main._cluster")
@patch("main.kube_list")
def test_limitranges_empty(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[])

    resp = client.get("/clusters/ns/cluster/limitranges")
    assert resp.status_code == 200
    assert resp.json()["limitranges"] == []
