"""Tests for PVC resize endpoint (Loop 12)."""
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
def test_resize_pvc_requires_confirm(mock_kpatch, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch("/clusters/ns/cluster/pvcs/my-pvc?namespace=default",
                     json={"storage": "50Gi"})
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True
    mock_kpatch.assert_not_called()


@patch("main._cluster")
@patch("main.kube_patch")
def test_resize_pvc_with_token_patches_storage(mock_kpatch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kpatch.side_effect = AsyncMock(return_value={})
    body = {"storage": "50Gi"}
    r1 = client.patch("/clusters/ns/cluster/pvcs/my-pvc?namespace=default", json=body)
    token = r1.json()["token"]
    r2 = client.patch(f"/clusters/ns/cluster/pvcs/my-pvc?namespace=default&token={token}", json=body)
    assert r2.json()["ok"] is True
    assert r2.json()["storage"] == "50Gi"
    patch_body = mock_kpatch.call_args[0][4]
    assert patch_body["spec"]["resources"]["requests"]["storage"] == "50Gi"


@patch("main._cluster")
def test_resize_pvc_missing_storage_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch("/clusters/ns/cluster/pvcs/my-pvc?namespace=default", json={})
    assert r.status_code == 400


@patch("main._cluster")
@patch("main.kube_patch")
def test_resize_pvc_percentage_value(mock_kpatch, mock_cluster):
    """Any string value is accepted — k8s validates format."""
    _setup_cluster(mock_cluster)
    mock_kpatch.side_effect = AsyncMock(return_value={})
    body = {"storage": "100Gi"}
    r1 = client.patch("/clusters/ns/cluster/pvcs/data-pvc?namespace=production", json=body)
    token = r1.json()["token"]
    r2 = client.patch(f"/clusters/ns/cluster/pvcs/data-pvc?namespace=production&token={token}", json=body)
    assert r2.json()["ok"] is True
    assert r2.json()["name"] == "data-pvc"
