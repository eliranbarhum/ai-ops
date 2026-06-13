"""Tests for Namespace Wizard (Loop 5): create with quota + limitrange."""
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


@patch("main._cluster")
@patch("main.kube_apply")
@patch("main.audit_emit")
def test_create_namespace_requires_confirm(mock_audit, mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/namespaces", json={"name": "my-ns"})
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True
    assert "token" in r.json()
    mock_apply.assert_not_called()


@patch("main._cluster")
@patch("main.kube_apply")
@patch("main.audit_emit")
def test_create_namespace_with_token_creates(mock_audit, mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    mock_audit.side_effect = AsyncMock(return_value=None)
    r1 = client.post("/clusters/ns/cluster/namespaces", json={"name": "my-ns"})
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/namespaces?token={token}", json={"name": "my-ns"})
    assert r2.json()["ok"] is True
    mock_apply.assert_called_once()
    manifest = mock_apply.call_args[0][1]
    assert manifest["kind"] == "Namespace"
    assert manifest["metadata"]["name"] == "my-ns"


@patch("main._cluster")
@patch("main.kube_apply")
@patch("main.audit_emit")
def test_create_namespace_with_quota_preset(mock_audit, mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    mock_audit.side_effect = AsyncMock(return_value=None)
    r1 = client.post("/clusters/ns/cluster/namespaces",
                     json={"name": "my-ns", "quota_preset": "small"})
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/namespaces?token={token}",
                     json={"name": "my-ns", "quota_preset": "small"})
    assert r2.json()["ok"] is True
    # Should have called kube_apply twice: namespace + quota
    assert mock_apply.call_count == 2
    kinds = [call[0][1]["kind"] for call in mock_apply.call_args_list]
    assert "Namespace" in kinds
    assert "ResourceQuota" in kinds


@patch("main._cluster")
@patch("main.kube_apply")
@patch("main.audit_emit")
def test_create_namespace_with_limits_preset(mock_audit, mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    mock_audit.side_effect = AsyncMock(return_value=None)
    r1 = client.post("/clusters/ns/cluster/namespaces",
                     json={"name": "my-ns", "limits_preset": "medium"})
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/namespaces?token={token}",
                     json={"name": "my-ns", "limits_preset": "medium"})
    assert r2.json()["ok"] is True
    kinds = [call[0][1]["kind"] for call in mock_apply.call_args_list]
    assert "LimitRange" in kinds


@patch("main._cluster")
@patch("main.kube_apply")
@patch("main.audit_emit")
def test_create_namespace_with_all_options(mock_audit, mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    mock_audit.side_effect = AsyncMock(return_value=None)
    r1 = client.post("/clusters/ns/cluster/namespaces",
                     json={"name": "my-ns", "quota_preset": "large", "limits_preset": "large",
                           "labels": {"team": "ops"}})
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/namespaces?token={token}",
                     json={"name": "my-ns", "quota_preset": "large", "limits_preset": "large",
                           "labels": {"team": "ops"}})
    assert r2.json()["ok"] is True
    assert mock_apply.call_count == 3  # namespace + quota + limitrange
    ns_call = next(c for c in mock_apply.call_args_list if c[0][1]["kind"] == "Namespace")
    assert ns_call[0][1]["metadata"]["labels"]["team"] == "ops"


@patch("main._cluster")
def test_create_namespace_missing_name_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/namespaces", json={})
    assert r.status_code == 400
