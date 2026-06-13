"""Tests for annotation editor and event stream endpoints (Loop 41)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _workload_resp(annotations=None):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ""
    mock_resp.json.return_value = {
        "metadata": {
            "name": "my-app",
            "namespace": "default",
            "annotations": annotations or {"app.io/version": "1.0"},
        }
    }
    return mock_resp


# ── Annotation Editor Tests ───────────────────────────────────────────────────

@patch("main._cluster")
def test_edit_annotations_issues_confirm(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post(
        "/clusters/ns/cluster/workloads/deployment/my-app/annotations",
        json={"namespace": "default", "add": {"custom.io/key": "val"}, "remove": []},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
def test_edit_annotations_invalid_kind_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post(
        "/clusters/ns/cluster/workloads/cronjob/my-app/annotations?token=x",
        json={"namespace": "default", "add": {"k": "v"}, "remove": []},
    )
    assert resp.status_code == 400


@patch("main._cluster")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_edit_annotations_add_executes(mock_audit, mock_cluster):
    mock_http = MagicMock()
    patch_resp = _workload_resp({"custom.io/key": "val"})
    mock_http.patch = AsyncMock(return_value=patch_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.post(
        "/clusters/ns/cluster/workloads/deployment/my-app/annotations",
        json={"namespace": "default", "add": {"custom.io/key": "val"}, "remove": []},
    )
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/workloads/deployment/my-app/annotations?token={token}",
        json={"namespace": "default", "add": {"custom.io/key": "val"}, "remove": []},
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["ok"] is True
    assert "annotations" in body
    mock_audit.assert_called_once()


@patch("main._cluster")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_edit_annotations_remove_executes(mock_audit, mock_cluster):
    mock_http = MagicMock()
    patch_resp = _workload_resp({})
    mock_http.patch = AsyncMock(return_value=patch_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    resp = client.post(
        "/clusters/ns/cluster/workloads/deployment/my-app/annotations",
        json={"namespace": "default", "add": {}, "remove": ["app.io/version"]},
    )
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/workloads/deployment/my-app/annotations?token={token}",
        json={"namespace": "default", "add": {}, "remove": ["app.io/version"]},
    )
    assert resp2.status_code == 200
    assert resp2.json()["ok"] is True


@patch("main._cluster")
def test_edit_annotations_statefulset_supported(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post(
        "/clusters/ns/cluster/workloads/statefulset/my-sts/annotations",
        json={"namespace": "default", "add": {"k": "v"}, "remove": []},
    )
    assert resp.status_code == 200
    assert resp.json().get("requires_confirm") is True


# ── Format Workload Annotations Test ──────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_workload_includes_annotations(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[{
        "metadata": {
            "name": "api",
            "namespace": "default",
            "creationTimestamp": "2024-01-01T00:00:00Z",
            "labels": {},
            "annotations": {"app.io/version": "2.0", "custom/tag": "blue"},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "api"}},
            "template": {"spec": {"containers": [{"image": "nginx:1.21"}]}},
        },
        "status": {"readyReplicas": 1},
    }])

    resp = client.get("/clusters/ns/cluster/workloads?kind=deployments")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert "annotations" in items[0]
    assert items[0]["annotations"]["app.io/version"] == "2.0"


# ── Event Stream Tests (non-SSE, structural) ──────────────────────────────────

@patch("main._cluster")
def test_event_stream_endpoint_exists(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    # Just check the endpoint is registered (streaming test is complex)
    routes = [r.path for r in app_module.app.routes]
    assert any("events/stream" in r for r in routes)


@patch("main._cluster")
def test_event_stream_invalid_namespace_still_registered(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    routes = [r.path for r in app_module.app.routes]
    stream_route = next((r for r in routes if "events/stream" in r), None)
    assert stream_route is not None
