"""Tests for CronJob suspend/unsuspend endpoint (Loop 11)."""
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
def test_suspend_cronjob_requires_confirm(mock_kpatch, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/cronjobs/my-cj/suspend?namespace=default&suspend=true")
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True
    mock_kpatch.assert_not_called()


@patch("main._cluster")
@patch("main.kube_patch")
def test_suspend_cronjob_with_token_patches_suspend_true(mock_kpatch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kpatch.side_effect = AsyncMock(return_value={})
    r1 = client.post("/clusters/ns/cluster/cronjobs/my-cj/suspend?namespace=default&suspend=true")
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/cronjobs/my-cj/suspend?namespace=default&suspend=true&token={token}")
    assert r2.json()["ok"] is True
    assert r2.json()["suspended"] is True
    patch_body = mock_kpatch.call_args[0][4]
    assert patch_body["spec"]["suspend"] is True


@patch("main._cluster")
@patch("main.kube_patch")
def test_unsuspend_cronjob_with_token_patches_suspend_false(mock_kpatch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kpatch.side_effect = AsyncMock(return_value={})
    r1 = client.post("/clusters/ns/cluster/cronjobs/my-cj/suspend?namespace=default&suspend=false")
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/cronjobs/my-cj/suspend?namespace=default&suspend=false&token={token}")
    assert r2.json()["ok"] is True
    assert r2.json()["suspended"] is False
    patch_body = mock_kpatch.call_args[0][4]
    assert patch_body["spec"]["suspend"] is False
