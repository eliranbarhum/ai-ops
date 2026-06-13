"""Tests for Secret lifecycle endpoints (Loop 4): create + patch."""
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


# ── Create secret ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_apply")
def test_secret_create_requires_confirm(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post(
        "/clusters/ns/cluster/secrets?namespace=default",
        json={"name": "my-secret", "type": "Opaque", "data": {"key": "value"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
@patch("main.kube_apply")
def test_secret_create_with_token_applies(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    r1 = client.post(
        "/clusters/ns/cluster/secrets?namespace=default",
        json={"name": "my-secret", "type": "Opaque", "data": {"key": "value"}},
    )
    token = r1.json()["token"]
    r2 = client.post(
        f"/clusters/ns/cluster/secrets?namespace=default&token={token}",
        json={"name": "my-secret", "type": "Opaque", "data": {"key": "value"}},
    )
    assert r2.json()["ok"] is True
    assert r2.json()["name"] == "my-secret"
    mock_apply.assert_called_once()
    manifest = mock_apply.call_args[0][1]
    assert manifest["kind"] == "Secret"
    assert manifest["type"] == "Opaque"
    assert "key" in manifest["data"]


@patch("main._cluster")
def test_secret_create_missing_name_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post(
        "/clusters/ns/cluster/secrets?namespace=default",
        json={"type": "Opaque", "data": {"key": "val"}},
    )
    assert r.status_code == 400


@patch("main._cluster")
def test_secret_create_missing_data_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.post(
        "/clusters/ns/cluster/secrets?namespace=default",
        json={"name": "my-secret", "type": "Opaque"},
    )
    assert r.status_code == 400


@patch("main._cluster")
@patch("main.kube_apply")
def test_secret_create_tls_type(mock_apply, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_apply.side_effect = AsyncMock(return_value={})
    r1 = client.post(
        "/clusters/ns/cluster/secrets?namespace=default",
        json={"name": "my-tls", "type": "kubernetes.io/tls",
              "data": {"tls.crt": "certdata", "tls.key": "keydata"}},
    )
    token = r1.json()["token"]
    r2 = client.post(
        f"/clusters/ns/cluster/secrets?namespace=default&token={token}",
        json={"name": "my-tls", "type": "kubernetes.io/tls",
              "data": {"tls.crt": "certdata", "tls.key": "keydata"}},
    )
    assert r2.json()["ok"] is True
    manifest = mock_apply.call_args[0][1]
    assert manifest["type"] == "kubernetes.io/tls"


# ── Patch secret ──────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_patch")
def test_secret_patch_requires_confirm(mock_kube_patch, mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch(
        "/clusters/ns/cluster/secrets/my-secret?namespace=default",
        json={"data": {"key": "newvalue"}},
    )
    assert r.status_code == 200
    assert r.json().get("requires_confirm") is True


@patch("main._cluster")
@patch("main.kube_patch")
def test_secret_patch_with_token_applies(mock_kube_patch, mock_cluster):
    _setup_cluster(mock_cluster)
    mock_kube_patch.side_effect = AsyncMock(return_value={})
    r1 = client.patch(
        "/clusters/ns/cluster/secrets/my-secret?namespace=default",
        json={"data": {"key": "newvalue"}},
    )
    token = r1.json()["token"]
    r2 = client.patch(
        f"/clusters/ns/cluster/secrets/my-secret?namespace=default&token={token}",
        json={"data": {"key": "newvalue"}},
    )
    assert r2.json()["ok"] is True
    mock_kube_patch.assert_called_once()
    patch_body = mock_kube_patch.call_args[0][4]
    assert "key" in patch_body["data"]


@patch("main._cluster")
def test_secret_patch_missing_data_returns_400(mock_cluster):
    _setup_cluster(mock_cluster)
    r = client.patch(
        "/clusters/ns/cluster/secrets/my-secret?namespace=default",
        json={},
    )
    assert r.status_code == 400
