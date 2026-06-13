"""Tests for namespace label PATCH endpoint (Loop 13)."""
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
@patch("main.kube_patch")
def test_patch_ns_labels_requires_confirm(mock_kpatch, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch("/clusters/ns/cluster/namespaces/production/labels",
                     json={"labels": {"team": "ops", "env": "prod"}})
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True
    mock_kpatch.assert_not_called()


@patch("main._cluster")
@patch("main.kube_patch")
def test_patch_ns_labels_with_token_applies(mock_kpatch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kpatch.side_effect = AsyncMock(return_value={})
    body = {"labels": {"team": "platform", "env": "production"}}
    r1 = client.patch("/clusters/ns/cluster/namespaces/my-ns/labels", json=body)
    token = r1.json()["token"]
    r2 = client.patch(f"/clusters/ns/cluster/namespaces/my-ns/labels?token={token}", json=body)
    assert r2.json()["ok"] is True
    assert r2.json()["name"] == "my-ns"
    patch_body = mock_kpatch.call_args[0][4]
    assert patch_body["metadata"]["labels"]["team"] == "platform"
    assert patch_body["metadata"]["labels"]["env"] == "production"


@patch("main._cluster")
@patch("main.kube_patch")
def test_patch_ns_labels_psa_label(mock_kpatch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kpatch.side_effect = AsyncMock(return_value={})
    body = {"labels": {"pod-security.kubernetes.io/enforce": "restricted"}}
    r1 = client.patch("/clusters/ns/cluster/namespaces/secure-ns/labels", json=body)
    token = r1.json()["token"]
    r2 = client.patch(f"/clusters/ns/cluster/namespaces/secure-ns/labels?token={token}", json=body)
    assert r2.json()["ok"] is True
    patch_body = mock_kpatch.call_args[0][4]
    assert patch_body["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"


@patch("main._cluster")
def test_patch_ns_labels_empty_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch("/clusters/ns/cluster/namespaces/my-ns/labels", json={"labels": {}})
    assert r.status_code == 400


@patch("main._cluster")
def test_patch_ns_labels_missing_labels_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch("/clusters/ns/cluster/namespaces/my-ns/labels", json={})
    assert r.status_code == 400
