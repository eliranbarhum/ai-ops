"""Tests for PDB write path (Loop 9): create + delete."""
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


# ── Create PDB ────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_apply")
def test_create_pdb_requires_confirm(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/pdbs?namespace=default",
                    json={"name": "web-pdb", "selector": {"app": "web"}, "min_available": 1})
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True
    mock_apply.assert_not_called()


@patch("main._cluster")
@patch("main.kube_apply")
def test_create_pdb_min_available_with_token(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    body = {"name": "web-pdb", "selector": {"app": "web"}, "min_available": 2}
    r1 = client.post("/clusters/ns/cluster/pdbs?namespace=default", json=body)
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/pdbs?namespace=default&token={token}", json=body)
    assert r2.json()["ok"] is True
    manifest = mock_apply.call_args[0][1]
    assert manifest["kind"] == "PodDisruptionBudget"
    assert manifest["spec"]["minAvailable"] == 2
    assert manifest["spec"]["selector"]["matchLabels"] == {"app": "web"}


@patch("main._cluster")
@patch("main.kube_apply")
def test_create_pdb_max_unavailable_with_token(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    body = {"name": "db-pdb", "selector": {"app": "postgres"}, "max_unavailable": "50%"}
    r1 = client.post("/clusters/ns/cluster/pdbs?namespace=default", json=body)
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/pdbs?namespace=default&token={token}", json=body)
    assert r2.json()["ok"] is True
    manifest = mock_apply.call_args[0][1]
    assert manifest["spec"]["maxUnavailable"] == "50%"
    assert "minAvailable" not in manifest["spec"]


@patch("main._cluster")
def test_create_pdb_missing_name_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/pdbs?namespace=default",
                    json={"selector": {"app": "web"}, "min_available": 1})
    assert r.status_code == 400


@patch("main._cluster")
def test_create_pdb_missing_selector_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/pdbs?namespace=default",
                    json={"name": "my-pdb", "min_available": 1})
    assert r.status_code == 400


@patch("main._cluster")
def test_create_pdb_missing_policy_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/pdbs?namespace=default",
                    json={"name": "my-pdb", "selector": {"app": "x"}})
    assert r.status_code == 400


# ── Delete PDB ────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_delete")
def test_delete_pdb_requires_confirm(mock_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.delete("/clusters/ns/cluster/pdbs/my-pdb?namespace=default")
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_delete")
def test_delete_pdb_with_token(mock_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_delete.side_effect = AsyncMock(return_value={})
    r1 = client.delete("/clusters/ns/cluster/pdbs/my-pdb?namespace=default")
    token = r1.json()["token"]
    r2 = client.delete(f"/clusters/ns/cluster/pdbs/my-pdb?namespace=default&token={token}")
    assert r2.json()["ok"] is True
    mock_delete.assert_called_once()
