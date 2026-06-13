"""Tests for GET /clusters/{id}/serviceaccounts and GET /clusters/{id}/rbac endpoints."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)

_SA_OBJ = {
    "metadata": {"name": "default", "namespace": "default", "creationTimestamp": "2024-01-01T00:00:00Z"},
    "secrets": [{"name": "default-token-abc"}],
    "imagePullSecrets": [{"name": "registry-creds"}],
}

_RB_OBJ = {
    "metadata": {"name": "view-binding", "namespace": "default", "creationTimestamp": "2024-01-01T00:00:00Z"},
    "roleRef": {"kind": "ClusterRole", "name": "view", "apiGroup": "rbac.authorization.k8s.io"},
    "subjects": [
        {"kind": "ServiceAccount", "name": "default", "namespace": "default"},
        {"kind": "User", "name": "alice"},
    ],
}


# ── ServiceAccounts ───────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_serviceaccounts_returns_list(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, resource, ns=None): return [_SA_OBJ]
    mock_kube_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/serviceaccounts")
    assert resp.status_code == 200
    assert "serviceaccounts" in resp.json()
    assert len(resp.json()["serviceaccounts"]) == 1


@patch("main._cluster")
@patch("main.kube_list")
def test_serviceaccounts_format(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, resource, ns=None): return [_SA_OBJ]
    mock_kube_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/serviceaccounts")
    sa = resp.json()["serviceaccounts"][0]
    assert sa["name"] == "default"
    assert sa["namespace"] == "default"
    assert sa["secrets_count"] == 1
    assert sa["image_pull_secrets"] == ["registry-creds"]


@patch("main._cluster")
@patch("main.kube_list")
def test_serviceaccounts_empty(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, resource, ns=None): return []
    mock_kube_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/serviceaccounts")
    assert resp.json()["serviceaccounts"] == []


# ── RBAC ─────────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_rbac_returns_bindings(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, resource, ns=None):
        if resource == "rolebindings": return [_RB_OBJ]
        return []  # clusterrolebindings
    mock_kube_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/rbac")
    assert resp.status_code == 200
    body = resp.json()
    assert "rolebindings" in body
    assert "clusterrolebindings" in body
    assert len(body["rolebindings"]) == 1


@patch("main._cluster")
@patch("main.kube_list")
def test_rbac_rolebinding_format(mock_kube_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    async def fake_list(client, resource, ns=None):
        if resource == "rolebindings": return [_RB_OBJ]
        return []
    mock_kube_list.side_effect = fake_list

    resp = client.get("/clusters/ns/cluster/rbac")
    rb = resp.json()["rolebindings"][0]
    assert rb["name"] == "view-binding"
    assert rb["role_ref_kind"] == "ClusterRole"
    assert rb["role_ref_name"] == "view"
    assert rb["subject_count"] == 2
    assert any(s["kind"] == "User" and s["name"] == "alice" for s in rb["subjects"])
