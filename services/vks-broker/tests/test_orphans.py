"""Tests for orphan resource detector (Loop 48)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _pod(name, ns, labels, phase="Running"):
    return {"metadata": {"name": name, "namespace": ns, "labels": labels},
            "status": {"phase": phase}, "spec": {"volumes": [], "containers": []}}


def _svc(name, ns, selector, svc_type="ClusterIP"):
    return {"metadata": {"name": name, "namespace": ns, "creationTimestamp": "2024-01-01T00:00:00Z"},
            "spec": {"selector": selector, "type": svc_type}}


def _pvc(name, ns, phase="Bound", storage="1Gi"):
    return {"metadata": {"name": name, "namespace": ns, "creationTimestamp": "2024-01-01T00:00:00Z"},
            "status": {"phase": phase},
            "spec": {"resources": {"requests": {"storage": storage}}, "storageClassName": "standard"}}


def _ingress(name, ns, rules):
    return {"metadata": {"name": name, "namespace": ns, "creationTimestamp": "2024-01-01T00:00:00Z"},
            "spec": {"rules": rules}}


def _deploy(name, ns, replicas=1):
    return {"metadata": {"name": name, "namespace": ns, "creationTimestamp": "2024-01-01T00:00:00Z",
                          "annotations": {}},
            "spec": {"replicas": replicas}}


def _setup_mock(mock_list, pods=None, services=None, pvcs=None, ingresses=None,
                deployments=None, hpas=None):
    data = {
        "pods": pods or [], "services": services or [], "pvcs": pvcs or [],
        "ingresses": ingresses or [], "deployments": deployments or [], "hpas": hpas or [],
    }
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))


# ── No orphans ────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_orphans_none(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup_mock(mock_list,
        pods=[_pod("api", "default", {"app": "api"})],
        services=[_svc("api-svc", "default", {"app": "api"})],
    )
    resp = client.get("/clusters/ns/cluster/orphans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total"] == 0


# ── Orphaned service ──────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_orphaned_service_detected(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup_mock(mock_list,
        pods=[_pod("api", "default", {"app": "api"})],
        services=[
            _svc("api-svc", "default", {"app": "api"}),       # has match
            _svc("ghost-svc", "default", {"app": "ghost"}),    # no match
        ],
    )
    resp = client.get("/clusters/ns/cluster/orphans")
    body = resp.json()
    assert body["summary"]["orphaned_services"] == 1
    assert body["orphaned_services"][0]["name"] == "ghost-svc"


# ── Headless service not flagged ──────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_headless_service_not_flagged(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup_mock(mock_list,
        pods=[],
        services=[_svc("headless", "default", {})],  # empty selector
    )
    resp = client.get("/clusters/ns/cluster/orphans")
    assert resp.json()["summary"]["orphaned_services"] == 0


# ── Unbound PVC ───────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_unbound_pvc_detected(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup_mock(mock_list,
        pods=[],
        pvcs=[_pvc("orphan-pvc", "default", phase="Released")],
    )
    resp = client.get("/clusters/ns/cluster/orphans")
    body = resp.json()
    assert body["summary"]["unbound_pvcs"] == 1
    assert body["unbound_pvcs"][0]["name"] == "orphan-pvc"
    assert body["unbound_pvcs"][0]["phase"] == "Released"


# ── Bound but unmounted PVC ───────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_bound_unmounted_pvc_flagged(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup_mock(mock_list,
        pods=[],
        pvcs=[_pvc("unused-pvc", "default", phase="Bound")],
    )
    resp = client.get("/clusters/ns/cluster/orphans")
    body = resp.json()
    assert body["summary"]["unbound_pvcs"] == 1
    assert "not mounted" in body["unbound_pvcs"][0]["reason"]


# ── Mounted PVC not flagged ───────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_mounted_pvc_not_flagged(mock_list, mock_cluster):
    pod_with_pvc = {
        "metadata": {"name": "db", "namespace": "default", "labels": {}},
        "status": {"phase": "Running"},
        "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": "db-pvc"}}], "containers": []},
    }
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup_mock(mock_list,
        pods=[pod_with_pvc],
        pvcs=[_pvc("db-pvc", "default", phase="Bound")],
    )
    resp = client.get("/clusters/ns/cluster/orphans")
    assert resp.json()["summary"]["unbound_pvcs"] == 0


# ── Orphaned ingress ──────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_orphaned_ingress_detected(mock_list, mock_cluster):
    ing = _ingress("my-ing", "default", [
        {"http": {"paths": [{"backend": {"service": {"name": "missing-svc"}}, "path": "/"}]}}
    ])
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup_mock(mock_list, ingresses=[ing])
    resp = client.get("/clusters/ns/cluster/orphans")
    body = resp.json()
    assert body["summary"]["orphaned_ingresses"] == 1
    assert "missing-svc" in body["orphaned_ingresses"][0]["missing_services"]


# ── Zero replica deployment ───────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_zero_replica_deployment_flagged(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup_mock(mock_list, deployments=[_deploy("scaled-down", "default", replicas=0)])
    resp = client.get("/clusters/ns/cluster/orphans")
    body = resp.json()
    assert body["summary"]["zero_replica_deployments"] == 1
    assert body["zero_replica_deployments"][0]["name"] == "scaled-down"


# ── Summary structure ─────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_orphans_response_structure(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup_mock(mock_list)
    resp = client.get("/clusters/ns/cluster/orphans")
    body = resp.json()
    assert "orphaned_services" in body
    assert "unbound_pvcs" in body
    assert "orphaned_ingresses" in body
    assert "zero_replica_deployments" in body
    assert "summary" in body
    s = body["summary"]
    assert "total" in s and "orphaned_services" in s and "unbound_pvcs" in s
