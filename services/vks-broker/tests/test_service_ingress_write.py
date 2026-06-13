"""Tests for Service edit + Ingress create/delete endpoints (Loop 3)."""
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


# ── Service patch ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_patch")
def test_service_patch_requires_confirm(mock_kube_patch, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch(
        "/clusters/ns/cluster/services/my-svc?namespace=default",
        json={"type": "NodePort"},
    )
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True
    assert "token" in r.json()


@patch("main._cluster")
@patch("main.kube_patch")
def test_service_patch_with_token_applies(mock_kube_patch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kube_patch.side_effect = AsyncMock(return_value={})
    r1 = client.patch(
        "/clusters/ns/cluster/services/my-svc?namespace=default",
        json={"type": "NodePort"},
    )
    token = r1.json()["token"]
    r2 = client.patch(
        f"/clusters/ns/cluster/services/my-svc?namespace=default&token={token}",
        json={"type": "NodePort"},
    )
    assert r2.json()["ok"] is True
    mock_kube_patch.assert_called_once()
    assert mock_kube_patch.call_args[0][4]["spec"]["type"] == "NodePort"


@patch("main._cluster")
def test_service_patch_missing_body_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch(
        "/clusters/ns/cluster/services/my-svc?namespace=default",
        json={},
    )
    assert r.status_code == 400


# ── Service delete ────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_delete")
def test_service_delete_requires_confirm(mock_kube_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.delete("/clusters/ns/cluster/services/my-svc?namespace=default")
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_delete")
def test_service_delete_with_token_deletes(mock_kube_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kube_delete.side_effect = AsyncMock(return_value={})
    r1 = client.delete("/clusters/ns/cluster/services/my-svc?namespace=default")
    token = r1.json()["token"]
    r2 = client.delete(f"/clusters/ns/cluster/services/my-svc?namespace=default&token={token}")
    assert r2.json()["ok"] is True
    mock_kube_delete.assert_called_once()


# ── Ingress create ────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_apply")
def test_ingress_create_requires_confirm(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post(
        "/clusters/ns/cluster/ingresses?namespace=default",
        json={"name": "my-ing", "host": "app.example.com", "service_name": "my-svc", "service_port": 80},
    )
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_apply")
def test_ingress_create_with_token_applies(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    r1 = client.post(
        "/clusters/ns/cluster/ingresses?namespace=default",
        json={"name": "my-ing", "host": "app.example.com", "service_name": "my-svc", "service_port": 80},
    )
    token = r1.json()["token"]
    r2 = client.post(
        f"/clusters/ns/cluster/ingresses?namespace=default&token={token}",
        json={"name": "my-ing", "host": "app.example.com", "service_name": "my-svc", "service_port": 80},
    )
    assert r2.json()["ok"] is True
    assert r2.json()["name"] == "my-ing"
    mock_apply.assert_called_once()
    manifest = mock_apply.call_args[0][1]
    assert manifest["kind"] == "Ingress"
    assert manifest["spec"]["rules"][0]["host"] == "app.example.com"


@patch("main._cluster")
def test_ingress_create_missing_required_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post(
        "/clusters/ns/cluster/ingresses?namespace=default",
        json={"name": "my-ing"},
    )
    assert r.status_code == 400


# ── Ingress delete ────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_delete")
def test_ingress_delete_requires_confirm(mock_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.delete("/clusters/ns/cluster/ingresses/my-ing?namespace=default")
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_delete")
def test_ingress_delete_with_token_deletes(mock_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_delete.side_effect = AsyncMock(return_value={})
    r1 = client.delete("/clusters/ns/cluster/ingresses/my-ing?namespace=default")
    token = r1.json()["token"]
    r2 = client.delete(f"/clusters/ns/cluster/ingresses/my-ing?namespace=default&token={token}")
    assert r2.json()["ok"] is True
    mock_delete.assert_called_once()
