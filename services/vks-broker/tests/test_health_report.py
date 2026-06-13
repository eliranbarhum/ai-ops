"""Tests for GET /clusters/{id}/health-report (Loop 35)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)

_NODE_READY = {
    "metadata": {"name": "worker-1", "labels": {"node-role.kubernetes.io/worker": ""}},
    "spec": {"taints": []},
    "status": {
        "conditions": [{"type": "Ready", "status": "True"}],
        "allocatable": {"cpu": "4", "memory": "8Gi"},
    },
}

_NODE_NOT_READY = {
    "metadata": {"name": "worker-2", "labels": {}},
    "spec": {"taints": [{"key": "node.kubernetes.io/not-ready", "effect": "NoSchedule"}]},
    "status": {
        "conditions": [{"type": "Ready", "status": "False"}],
        "allocatable": {"cpu": "4", "memory": "8Gi"},
    },
}

_POD_RUNNING = {
    "metadata": {"name": "app-1", "namespace": "default"},
    "status": {"phase": "Running", "containerStatuses": []},
}

_POD_CRASHLOOP = {
    "metadata": {"name": "bad-app", "namespace": "default"},
    "status": {
        "phase": "Running",
        "containerStatuses": [{"state": {"waiting": {"reason": "CrashLoopBackOff"}}, "restartCount": 10}],
    },
}

_POD_FAILED = {
    "metadata": {"name": "failed-pod", "namespace": "default"},
    "status": {"phase": "Failed", "containerStatuses": []},
}

_DEPLOYMENT_HEALTHY = {
    "metadata": {"name": "my-dep", "namespace": "default"},
    "spec": {"replicas": 3},
    "status": {"readyReplicas": 3},
}

_DEPLOYMENT_DEGRADED = {
    "metadata": {"name": "bad-dep", "namespace": "default"},
    "spec": {"replicas": 3},
    "status": {"readyReplicas": 1},
}

_WARNING_EVENT = {
    "metadata": {"name": "ev1", "namespace": "default"},
    "type": "Warning",
    "reason": "BackOff",
    "message": "Back-off restarting failed container",
    "involvedObject": {"kind": "Pod", "name": "bad-app", "namespace": "default"},
    "count": 42,
    "lastTimestamp": "2024-01-01T10:00:00Z",
    "source": {"component": "kubelet"},
}

_QUOTA = {
    "metadata": {"name": "default-quota", "namespace": "default", "creationTimestamp": "2024-01-01T00:00:00Z"},
    "spec": {"hard": {"cpu": "10", "memory": "20Gi"}},
    "status": {
        "hard": {"cpu": "10", "memory": "20Gi"},
        "used": {"cpu": "9", "memory": "5Gi"},
    },
}


@patch("main._cluster")
@patch("main.kube_list")
def test_health_report_structure(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [_NODE_READY],
        "pods": [_POD_RUNNING],
        "deployments": [_DEPLOYMENT_HEALTHY],
        "statefulsets": [],
        "daemonsets": [],
        "events": [_WARNING_EVENT],
        "resourcequotas": [_QUOTA],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/health-report")
    assert resp.status_code == 200
    body = resp.json()
    assert "generated_at" in body
    assert "cluster_id" in body
    assert "nodes" in body
    assert "pods" in body
    assert "workloads" in body
    assert "crashloop_pods" in body
    assert "quota_pressure" in body
    assert "warning_events" in body
    assert "summary" in body


@patch("main._cluster")
@patch("main.kube_list")
def test_health_report_node_summary(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [_NODE_READY, _NODE_NOT_READY],
        "pods": [], "deployments": [], "statefulsets": [], "daemonsets": [], "events": [], "resourcequotas": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/health-report")
    body = resp.json()
    assert body["summary"]["nodes_ready"] == 1
    assert body["summary"]["nodes_total"] == 2
    nodes = body["nodes"]
    assert len(nodes) == 2
    ready_nodes = [n for n in nodes if n["ready"]]
    assert len(ready_nodes) == 1
    assert nodes[1]["taints"] == 1


@patch("main._cluster")
@patch("main.kube_list")
def test_health_report_crashloop_detection(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [], "pods": [_POD_RUNNING, _POD_CRASHLOOP, _POD_FAILED],
        "deployments": [], "statefulsets": [], "daemonsets": [], "events": [], "resourcequotas": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/health-report")
    body = resp.json()
    assert body["summary"]["crashloop_count"] == 1
    assert body["crashloop_pods"][0]["name"] == "bad-app"
    assert body["pods"]["Failed"] == 1


@patch("main._cluster")
@patch("main.kube_list")
def test_health_report_degraded_workloads(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [], "pods": [],
        "deployments": [_DEPLOYMENT_HEALTHY, _DEPLOYMENT_DEGRADED],
        "statefulsets": [], "daemonsets": [], "events": [], "resourcequotas": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/health-report")
    body = resp.json()
    assert body["summary"]["degraded_workloads"] == 1
    assert body["workloads"]["degraded"][0]["name"] == "bad-dep"


@patch("main._cluster")
@patch("main.kube_list")
def test_health_report_quota_pressure(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [], "pods": [], "deployments": [], "statefulsets": [], "daemonsets": [],
        "events": [], "resourcequotas": [_QUOTA],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/health-report")
    body = resp.json()
    pressure = body["quota_pressure"]
    # cpu: 9/10 = 90% > 70%, so should appear
    cpu_entry = next((p for p in pressure if p["resource"] == "cpu"), None)
    assert cpu_entry is not None
    assert cpu_entry["pct"] == 90
    assert body["summary"]["quota_pressure_count"] >= 1


@patch("main._cluster")
@patch("main.kube_list")
def test_health_report_warning_events(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "nodes": [], "pods": [], "deployments": [], "statefulsets": [], "daemonsets": [],
        "events": [_WARNING_EVENT], "resourcequotas": [],
    }.get(kind, []))

    resp = client.get("/clusters/ns/cluster/health-report")
    body = resp.json()
    assert body["summary"]["warning_event_count"] == 1
    assert body["warning_events"][0]["reason"] == "BackOff"
