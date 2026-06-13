"""Tests for HPA write path (Loop 8): patch + create + delete."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _setup_cluster(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())


# ── Patch HPA ─────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_patch")
def test_patch_hpa_requires_confirm(mock_kpatch, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch("/clusters/ns/cluster/hpa/my-hpa?namespace=default",
                     json={"min_replicas": 2, "max_replicas": 10})
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True
    mock_kpatch.assert_not_called()


@patch("main._cluster")
@patch("main.kube_patch")
def test_patch_hpa_with_token_applies(mock_kpatch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kpatch.side_effect = AsyncMock(return_value={})
    body = {"min_replicas": 2, "max_replicas": 15, "target_cpu_pct": 70}
    r1 = client.patch("/clusters/ns/cluster/hpa/my-hpa?namespace=default", json=body)
    token = r1.json()["token"]
    r2 = client.patch(f"/clusters/ns/cluster/hpa/my-hpa?namespace=default&token={token}", json=body)
    assert r2.json()["ok"] is True
    patch_body = mock_kpatch.call_args[0][4]
    assert patch_body["spec"]["minReplicas"] == 2
    assert patch_body["spec"]["maxReplicas"] == 15
    assert patch_body["spec"]["metrics"][0]["resource"]["target"]["averageUtilization"] == 70


@patch("main._cluster")
def test_patch_hpa_no_fields_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch("/clusters/ns/cluster/hpa/my-hpa?namespace=default", json={})
    assert r.status_code == 400


# ── Create HPA ────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_apply")
def test_create_hpa_requires_confirm(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/hpa?namespace=default",
                    json={"name": "web-hpa", "target_name": "web", "max_replicas": 20})
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True
    mock_apply.assert_not_called()


@patch("main._cluster")
@patch("main.kube_apply")
def test_create_hpa_with_token_applies(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    body = {"name": "web-hpa", "target_name": "web", "max_replicas": 20, "min_replicas": 2, "target_cpu_pct": 60}
    r1 = client.post("/clusters/ns/cluster/hpa?namespace=default", json=body)
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/hpa?namespace=default&token={token}", json=body)
    assert r2.json()["ok"] is True
    manifest = mock_apply.call_args[0][1]
    assert manifest["kind"] == "HorizontalPodAutoscaler"
    assert manifest["spec"]["scaleTargetRef"]["name"] == "web"
    assert manifest["spec"]["maxReplicas"] == 20
    assert manifest["spec"]["metrics"][0]["resource"]["target"]["averageUtilization"] == 60


@patch("main._cluster")
def test_create_hpa_missing_required_fields_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/hpa?namespace=default",
                    json={"name": "bad-hpa"})  # missing target_name + max_replicas
    assert r.status_code == 400


# ── Delete HPA ────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_delete")
def test_delete_hpa_requires_confirm(mock_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.delete("/clusters/ns/cluster/hpa/my-hpa?namespace=default")
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_delete")
def test_delete_hpa_with_token(mock_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_delete.side_effect = AsyncMock(return_value={})
    r1 = client.delete("/clusters/ns/cluster/hpa/my-hpa?namespace=default")
    token = r1.json()["token"]
    r2 = client.delete(f"/clusters/ns/cluster/hpa/my-hpa?namespace=default&token={token}")
    assert r2.json()["ok"] is True
    mock_delete.assert_called_once()
