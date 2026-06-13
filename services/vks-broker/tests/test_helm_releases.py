"""Tests for Helm release browser endpoints (Loop 42)."""
import base64, gzip, json, pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _encode_release(rel: dict) -> str:
    compressed = gzip.compress(json.dumps(rel).encode())
    return base64.b64encode(compressed).decode()


def _make_secret(namespace: str, release_name: str, revision: int,
                 status: str = "deployed", chart: str = "mychart",
                 chart_version: str = "1.0.0", app_version: str = "2.0.0",
                 config: dict = None, manifest: str = "") -> dict:
    rel = {
        "name": release_name,
        "version": revision,
        "info": {
            "status": status,
            "first_deployed": "2024-01-01T00:00:00Z",
            "last_deployed": "2024-06-01T00:00:00Z",
            "description": "Install complete" if status == "deployed" else "Rollback complete",
        },
        "chart": {"metadata": {"name": chart, "version": chart_version, "appVersion": app_version}},
        "config": config or {},
        "manifest": manifest or f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {release_name}\n",
    }
    return {
        "metadata": {
            "name": f"sh.helm.release.v1.{release_name}.v{revision}",
            "namespace": namespace,
            "labels": {"owner": "helm"},
        },
        "data": {"release": _encode_release(rel)},
    }


def _secrets_resp(secrets: list) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"items": secrets}
    return mock


# ── List releases ─────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_list_helm_releases_empty(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_secrets_resp([]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/helm/releases")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["releases"] == []


@patch("main._cluster")
def test_list_helm_releases_single(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_secrets_resp([
        _make_secret("default", "myapp", 3, status="deployed"),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/helm/releases")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    r = body["releases"][0]
    assert r["name"] == "myapp"
    assert r["namespace"] == "default"
    assert r["revision"] == 3
    assert r["status"] == "deployed"
    assert r["chart_name"] == "mychart"
    assert r["chart_version"] == "1.0.0"
    assert r["app_version"] == "2.0.0"


@patch("main._cluster")
def test_list_helm_releases_latest_revision_only(mock_cluster):
    """When multiple revisions exist, only the latest is returned."""
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_secrets_resp([
        _make_secret("default", "myapp", 1, status="superseded"),
        _make_secret("default", "myapp", 2, status="superseded"),
        _make_secret("default", "myapp", 3, status="deployed"),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/helm/releases")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["releases"][0]["revision"] == 3
    assert body["releases"][0]["status"] == "deployed"


@patch("main._cluster")
def test_list_helm_releases_multiple_apps(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_secrets_resp([
        _make_secret("default", "app-a", 1),
        _make_secret("production", "app-b", 2),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/helm/releases")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


# ── Values ────────────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_helm_values_returns_yaml(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_secrets_resp([
        _make_secret("default", "myapp", 1, config={"replicaCount": 3, "image": {"tag": "v1.2"}}),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/helm/releases/default/myapp/values")
    assert resp.status_code == 200
    body = resp.json()
    assert "values_yaml" in body
    assert "replicaCount" in body["values_yaml"]
    assert body["revision"] == 1


@patch("main._cluster")
def test_helm_values_scrubs_passwords(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_secrets_resp([
        _make_secret("default", "myapp", 1, config={
            "db": {"password": "super-secret", "host": "db.svc"},
            "apiToken": "tok-12345",
            "replicas": 2,
        }),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/helm/releases/default/myapp/values")
    assert resp.status_code == 200
    yaml_text = resp.json()["values_yaml"]
    assert "super-secret" not in yaml_text
    assert "tok-12345" not in yaml_text
    assert "***" in yaml_text
    assert "db.svc" in yaml_text  # non-sensitive values preserved


@patch("main._cluster")
def test_helm_values_not_found_404(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_secrets_resp([]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/helm/releases/default/nonexistent/values")
    assert resp.status_code == 404


# ── History ───────────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_helm_history_ordered(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_secrets_resp([
        _make_secret("default", "myapp", 1, status="superseded"),
        _make_secret("default", "myapp", 2, status="superseded"),
        _make_secret("default", "myapp", 3, status="deployed"),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/helm/releases/default/myapp/history")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["history"]) == 3
    # newest first
    assert body["history"][0]["revision"] == 3
    assert body["history"][2]["revision"] == 1


# ── Manifest ──────────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_helm_manifest_returns_resource_count(mock_cluster):
    multi_manifest = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: myapp\n---\n"
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: myapp-svc\n"
    )
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_secrets_resp([
        _make_secret("default", "myapp", 1, manifest=multi_manifest),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/helm/releases/default/myapp/manifest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resource_count"] == 2
    assert "Deployment" in body["resource_kinds"]
    assert "Service" in body["resource_kinds"]
    assert "manifest" in body
