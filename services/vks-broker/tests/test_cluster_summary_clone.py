"""Tests for /summary and /namespaces/{ns}/clone endpoints (Loop 39)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _node(name, ready=True, cpu="4", mem="8Gi"):
    status = "True" if ready else "False"
    return {
        "metadata": {"name": name},
        "spec": {},
        "status": {
            "conditions": [{"type": "Ready", "status": status}],
            "allocatable": {"cpu": cpu, "memory": mem},
        },
    }


def _pod(phase="Running"):
    return {"metadata": {"name": "p", "namespace": "default"}, "status": {"phase": phase}}


def _deploy(name, replicas=2, ready=2):
    return {
        "metadata": {"name": name, "namespace": "default"},
        "spec": {"replicas": replicas},
        "status": {"readyReplicas": ready},
    }


def _ns(name):
    return {"metadata": {"name": name}, "status": {"phase": "Active"}}


@patch("main._cluster")
@patch("main.kube_list")
def test_summary_structure(mock_list, mock_cluster):
    version_mock = MagicMock()
    version_mock.json.return_value = {"major": "1", "minor": "28"}
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=version_mock)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [_node("w1"), _node("w2")],
        "pods": [_pod("Running"), _pod("Failed")],
        "deployments": [_deploy("app", 3, 3)],
        "statefulsets": [],
        "namespaces": [_ns("default"), _ns("prod")],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "version" in body
    assert "nodes" in body
    assert "pods" in body
    assert "workloads" in body
    assert "namespaces" in body
    assert "capacity" in body


@patch("main._cluster")
@patch("main.kube_list")
def test_summary_node_counts(mock_list, mock_cluster):
    version_mock = MagicMock()
    version_mock.json.return_value = {"major": "1", "minor": "28"}
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=version_mock)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [_node("w1", ready=True), _node("w2", ready=False)],
        "pods": [], "deployments": [], "statefulsets": [], "namespaces": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/summary")
    body = resp.json()
    assert body["nodes"]["total"] == 2
    assert body["nodes"]["ready"] == 1


@patch("main._cluster")
@patch("main.kube_list")
def test_summary_workload_health(mock_list, mock_cluster):
    version_mock = MagicMock()
    version_mock.json.return_value = {"major": "1", "minor": "28"}
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=version_mock)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [], "pods": [],
        "deployments": [_deploy("ok", 2, 2), _deploy("bad", 2, 0)],
        "statefulsets": [], "namespaces": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/summary")
    body = resp.json()
    assert body["workloads"]["total"] == 2
    assert body["workloads"]["healthy"] == 1


@patch("main._cluster")
@patch("main.kube_list")
def test_summary_capacity(mock_list, mock_cluster):
    version_mock = MagicMock()
    version_mock.json.return_value = {"major": "1", "minor": "28"}
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=version_mock)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)

    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [_node("w1", cpu="4", mem="8Gi"), _node("w2", cpu="4", mem="8Gi")],
        "pods": [], "deployments": [], "statefulsets": [], "namespaces": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/summary")
    body = resp.json()
    assert body["capacity"]["cpu_cores"] == 8.0
    assert body["capacity"]["memory_gib"] == 16.0


@patch("main._cluster")
def test_clone_issues_confirm(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post(
        "/clusters/ns/cluster/namespaces/default/clone",
        json={"target_namespace": "staging", "resource_types": ["configmaps"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("requires_confirm") is True
    assert "token" in body


@patch("main._cluster")
def test_clone_missing_target_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post(
        "/clusters/ns/cluster/namespaces/default/clone",
        json={"resource_types": ["configmaps"]},
    )
    assert resp.status_code == 400


@patch("main._cluster")
def test_clone_invalid_resource_type_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.post(
        "/clusters/ns/cluster/namespaces/default/clone",
        json={"target_namespace": "staging", "resource_types": ["deployments"]},
    )
    assert resp.status_code == 400


@patch("main._cluster")
@patch("main.kube_list")
@patch("main.audit_emit", new_callable=AsyncMock)
def test_clone_executes_with_token(mock_audit, mock_list, mock_cluster):
    mock_http = MagicMock()
    ns_check = MagicMock()
    ns_check.status_code = 200
    create_resp = MagicMock()
    create_resp.status_code = 201
    post_resp = MagicMock()
    post_resp.status_code = 201
    mock_http.get = AsyncMock(return_value=ns_check)
    mock_http.post = AsyncMock(return_value=post_resp)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    mock_list.side_effect = AsyncMock(return_value=[
        {"metadata": {"name": "app-config", "namespace": "default", "labels": {}}, "data": {"key": "val"}},
    ])

    resp = client.post(
        "/clusters/ns/cluster/namespaces/default/clone",
        json={"target_namespace": "staging", "resource_types": ["configmaps"]},
    )
    token = resp.json()["token"]

    resp2 = client.post(
        f"/clusters/ns/cluster/namespaces/default/clone?token={token}",
        json={"target_namespace": "staging", "resource_types": ["configmaps"]},
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["ok"] is True
    assert len(body["cloned"]) == 1
    assert "configmaps/app-config" in body["cloned"][0]
    mock_audit.assert_called_once()
