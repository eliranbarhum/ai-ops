"""Tests for RBAC write path (Loop 6): create/delete RoleBinding + explain."""
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


# ── ClusterRoles list ──────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_clusterroles_list(mock_list, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_list.side_effect = AsyncMock(return_value=[
        {"metadata": {"name": "admin"}, "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]},
        {"metadata": {"name": "view"}, "rules": [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get"]}]},
        {"metadata": {"name": "system:node"}, "rules": []},
    ])
    r = client.get("/clusters/ns/cluster/clusterroles")
    assert r.status_code == 200
    names = [cr["name"] for cr in r.json()["clusterroles"]]
    assert "admin" in names
    assert "view" in names
    assert "system:node" not in names  # filtered out


# ── Create RoleBinding ─────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_apply")
def test_create_rolebinding_requires_confirm(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post(
        "/clusters/ns/cluster/rolebindings?namespace=default",
        json={"name": "dev-binding", "subject_kind": "User",
              "subject_name": "alice", "role_ref_kind": "ClusterRole", "role_ref_name": "edit"},
    )
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True
    mock_apply.assert_not_called()


@patch("main._cluster")
@patch("main.kube_apply")
def test_create_rolebinding_with_token_applies(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    r1 = client.post(
        "/clusters/ns/cluster/rolebindings?namespace=default",
        json={"name": "dev-binding", "subject_kind": "User",
              "subject_name": "alice", "role_ref_kind": "ClusterRole", "role_ref_name": "edit"},
    )
    token = r1.json()["token"]
    r2 = client.post(
        f"/clusters/ns/cluster/rolebindings?namespace=default&token={token}",
        json={"name": "dev-binding", "subject_kind": "User",
              "subject_name": "alice", "role_ref_kind": "ClusterRole", "role_ref_name": "edit"},
    )
    assert r2.json()["ok"] is True
    manifest = mock_apply.call_args[0][1]
    assert manifest["kind"] == "RoleBinding"
    assert manifest["subjects"][0]["name"] == "alice"
    assert manifest["roleRef"]["name"] == "edit"


@patch("main._cluster")
@patch("main.kube_apply")
def test_create_clusterrolebinding(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    r1 = client.post(
        "/clusters/ns/cluster/rolebindings?namespace=default",
        json={"name": "cluster-admin-binding", "subject_kind": "User",
              "subject_name": "bob", "role_ref_name": "cluster-admin", "cluster_wide": True},
    )
    token = r1.json()["token"]
    r2 = client.post(
        f"/clusters/ns/cluster/rolebindings?namespace=default&token={token}",
        json={"name": "cluster-admin-binding", "subject_kind": "User",
              "subject_name": "bob", "role_ref_name": "cluster-admin", "cluster_wide": True},
    )
    assert r2.json()["ok"] is True
    manifest = mock_apply.call_args[0][1]
    assert manifest["kind"] == "ClusterRoleBinding"


@patch("main._cluster")
def test_create_rolebinding_missing_fields_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post(
        "/clusters/ns/cluster/rolebindings?namespace=default",
        json={"name": "bad"},
    )
    assert r.status_code == 400


# ── Delete RoleBinding ─────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_delete")
def test_delete_rolebinding_requires_confirm(mock_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.delete("/clusters/ns/cluster/rolebindings/my-binding?namespace=default")
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_delete")
def test_delete_rolebinding_with_token(mock_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_delete.side_effect = AsyncMock(return_value={})
    r1 = client.delete("/clusters/ns/cluster/rolebindings/my-binding?namespace=default")
    token = r1.json()["token"]
    r2 = client.delete(f"/clusters/ns/cluster/rolebindings/my-binding?namespace=default&token={token}")
    assert r2.json()["ok"] is True
    mock_delete.assert_called_once()


# ── Explain RoleBinding ───────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_get")
@patch("main.LLM_GATEWAY_URL", "http://mock-llm")
@patch("httpx.AsyncClient")
def test_explain_rolebinding_returns_event_stream(mock_http, mock_get, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_get.side_effect = AsyncMock(return_value={
        "roleRef": {"kind": "ClusterRole", "name": "edit"},
        "subjects": [{"kind": "User", "name": "alice"}],
        "rules": [],
    })
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"text": "The edit role grants full access..."}
    mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
    mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
    r = client.get("/clusters/ns/cluster/rolebindings/my-binding/explain?namespace=default")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
