"""Tests for /raw (YAML viewer) and /hpa endpoints."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _make_deployment_obj(name="web", namespace="default"):
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "creationTimestamp": "2026-01-01T00:00:00Z",
            "managedFields": [{"manager": "kubectl"}],  # should be stripped
        },
        "spec": {"replicas": 2},
        "status": {},
    }


def _make_hpa(name="web-hpa", namespace="default"):
    return {
        "metadata": {
            "name": name, "namespace": namespace,
            "creationTimestamp": "2026-01-01T00:00:00Z",
        },
        "spec": {
            "scaleTargetRef": {"kind": "Deployment", "name": "web"},
            "minReplicas": 2,
            "maxReplicas": 10,
            "targetCPUUtilizationPercentage": 80,
        },
        "status": {
            "currentReplicas": 3,
            "desiredReplicas": 5,
            "currentCPUUtilizationPercentage": 75,
            "conditions": [],
        },
    }


# ── GET /raw ──────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_get")
def test_raw_get_returns_yaml(mock_get, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_get(client, kind, name, ns): return _make_deployment_obj()
    mock_get.side_effect = fake_get

    resp = client.get("/clusters/ns/cluster/raw/deployments/web?namespace=default")
    assert resp.status_code == 200
    assert "yaml" in resp.json()
    assert "web" in resp.json()["yaml"]


@patch("main._cluster")
@patch("main.kube_get")
def test_raw_get_strips_managed_fields(mock_get, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_get(client, kind, name, ns): return _make_deployment_obj()
    mock_get.side_effect = fake_get

    resp = client.get("/clusters/ns/cluster/raw/deployments/web?namespace=default")
    assert "managedFields" not in resp.json()["yaml"]


# ── PUT /raw ──────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_apply")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_raw_put_applies_yaml(mock_audit, mock_apply, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_apply(client, doc): return {"metadata": {"name": "web"}}
    mock_apply.side_effect = fake_apply

    import yaml as _yaml
    yaml_str = _yaml.dump(_make_deployment_obj())
    resp = client.put(
        "/clusters/ns/cluster/raw/deployments/web?namespace=default",
        json={"yaml": yaml_str},
        headers={"x-forwarded-user": "alice"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"


@patch("main._cluster")
def test_raw_put_missing_yaml_returns_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.put(
        "/clusters/ns/cluster/raw/deployments/web?namespace=default",
        json={},
    )
    assert resp.status_code == 400


@patch("main._cluster")
def test_raw_put_invalid_yaml_returns_422(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.put(
        "/clusters/ns/cluster/raw/deployments/web",
        json={"yaml": "- not a mapping"},
    )
    assert resp.status_code == 422


# ── GET /hpa ──────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_hpa_list_returns_formatted(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())

    async def fake_list(client, kind, ns=None):
        return [_make_hpa()]

    mock_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/hpa?namespace=default")
    assert resp.status_code == 200
    data = resp.json()
    assert "hpa" in data
    assert len(data["hpa"]) == 1
    h = data["hpa"][0]
    assert h["name"] == "web-hpa"
    assert h["max_replicas"] == 10
    assert h["target_cpu_pct"] == 80
    assert h["current_replicas"] == 3


@patch("main._cluster")
@patch("main.kube_list")
def test_hpa_empty_list(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())

    async def fake_list(client, kind, ns=None):
        return []

    mock_list.side_effect = fake_list
    resp = client.get("/clusters/ns/cluster/hpa")
    assert resp.status_code == 200
    assert resp.json()["hpa"] == []
