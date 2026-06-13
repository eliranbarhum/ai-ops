"""Tests for ServiceAccount write path (Loop 10): create + delete."""
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


# ── Create SA ─────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_apply")
def test_create_sa_requires_confirm(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/serviceaccounts?namespace=default",
                    json={"name": "my-sa"})
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True
    mock_apply.assert_not_called()


@patch("main._cluster")
@patch("main.kube_apply")
def test_create_sa_with_token_applies(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    body = {"name": "my-sa"}
    r1 = client.post("/clusters/ns/cluster/serviceaccounts?namespace=default", json=body)
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/serviceaccounts?namespace=default&token={token}", json=body)
    assert r2.json()["ok"] is True
    manifest = mock_apply.call_args[0][1]
    assert manifest["kind"] == "ServiceAccount"
    assert manifest["metadata"]["name"] == "my-sa"
    assert "imagePullSecrets" not in manifest


@patch("main._cluster")
@patch("main.kube_apply")
def test_create_sa_with_pull_secrets(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    body = {"name": "ci-sa", "image_pull_secrets": ["harbor-creds", "gcr-secret"]}
    r1 = client.post("/clusters/ns/cluster/serviceaccounts?namespace=ci", json=body)
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/serviceaccounts?namespace=ci&token={token}", json=body)
    assert r2.json()["ok"] is True
    manifest = mock_apply.call_args[0][1]
    assert manifest["imagePullSecrets"] == [{"name": "harbor-creds"}, {"name": "gcr-secret"}]


@patch("main._cluster")
@patch("main.kube_apply")
def test_create_sa_with_labels(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    body = {"name": "labeled-sa", "labels": {"team": "platform"}}
    r1 = client.post("/clusters/ns/cluster/serviceaccounts?namespace=default", json=body)
    token = r1.json()["token"]
    r2 = client.post(f"/clusters/ns/cluster/serviceaccounts?namespace=default&token={token}", json=body)
    assert r2.json()["ok"] is True
    manifest = mock_apply.call_args[0][1]
    assert manifest["metadata"]["labels"]["team"] == "platform"


@patch("main._cluster")
def test_create_sa_missing_name_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post("/clusters/ns/cluster/serviceaccounts?namespace=default", json={})
    assert r.status_code == 400


# ── Delete SA ─────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_delete")
def test_delete_sa_requires_confirm(mock_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.delete("/clusters/ns/cluster/serviceaccounts/my-sa?namespace=default")
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_delete")
def test_delete_sa_with_token(mock_delete, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_delete.side_effect = AsyncMock(return_value={})
    r1 = client.delete("/clusters/ns/cluster/serviceaccounts/my-sa?namespace=default")
    token = r1.json()["token"]
    r2 = client.delete(f"/clusters/ns/cluster/serviceaccounts/my-sa?namespace=default&token={token}")
    assert r2.json()["ok"] is True
    mock_delete.assert_called_once()


@patch("main._cluster")
def test_delete_default_sa_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.delete("/clusters/ns/cluster/serviceaccounts/default?namespace=default")
    assert r.status_code == 400
