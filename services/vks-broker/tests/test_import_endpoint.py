"""Tests for the cluster import endpoint — specifically validates the kubeconfig_yaml field fix."""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub out broker/audit dependencies before importing main
with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

client = TestClient(app_module.app)


@patch("main.add_imported_cluster", new_callable=AsyncMock)
@patch("main.audit_emit", new_callable=AsyncMock)
def test_import_accepts_kubeconfig_yaml_field(mock_audit, mock_add):
    """UI posts 'kubeconfig_yaml' — broker must accept that field name."""
    mock_add.return_value = {"id": "imported/test", "name": "test", "server": "https://1.2.3.4:6443"}
    resp = client.post("/clusters/import", json={"name": "test", "kubeconfig_yaml": "apiVersion: v1\nkind: Config"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_add.assert_called_once_with("test", "apiVersion: v1\nkind: Config")


@patch("main.add_imported_cluster", new_callable=AsyncMock)
@patch("main.audit_emit", new_callable=AsyncMock)
def test_import_accepts_legacy_kubeconfig_field(mock_audit, mock_add):
    """Backwards compat: 'kubeconfig' field also accepted."""
    mock_add.return_value = {"id": "imported/test", "name": "test", "server": "https://1.2.3.4:6443"}
    resp = client.post("/clusters/import", json={"name": "test", "kubeconfig": "apiVersion: v1\nkind: Config"})
    assert resp.status_code == 200


def test_import_missing_name():
    resp = client.post("/clusters/import", json={"kubeconfig_yaml": "apiVersion: v1"})
    assert resp.status_code == 400
    assert "name" in resp.json()["detail"]


def test_import_missing_kubeconfig():
    resp = client.post("/clusters/import", json={"name": "test"})
    assert resp.status_code == 400
    assert "kubeconfig_yaml" in resp.json()["detail"]


@patch("main.add_imported_cluster", new_callable=AsyncMock)
@patch("main.audit_emit", new_callable=AsyncMock)
def test_import_invalid_kubeconfig_returns_422(mock_audit, mock_add):
    mock_add.side_effect = ValueError("invalid kubeconfig: no clusters defined")
    resp = client.post("/clusters/import", json={"name": "test", "kubeconfig_yaml": "not: valid: yaml: {"})
    assert resp.status_code == 422


@patch("main.remove_imported_cluster", new_callable=AsyncMock)
@patch("main.audit_emit", new_callable=AsyncMock)
def test_delete_imported(mock_audit, mock_remove):
    resp = client.delete("/clusters/import/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_remove.assert_called_once_with("imported/test")


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
