"""Tests for pending pod / scheduling issues analyzer (Loop 50)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _pod(name, ns="default", phase="Pending", sched_msg="", container_waitings=None, node_selector=None):
    conditions = []
    if sched_msg:
        conditions.append({"type": "PodScheduled", "status": "False", "message": sched_msg})
    container_statuses = []
    for cname, reason, msg in (container_waitings or []):
        container_statuses.append({"name": cname, "state": {"waiting": {"reason": reason, "message": msg}}, "restartCount": 0})
    return {
        "metadata": {"name": name, "namespace": ns, "creationTimestamp": "2024-01-01T00:00:00Z"},
        "status": {"phase": phase, "conditions": conditions, "containerStatuses": container_statuses, "initContainerStatuses": []},
        "spec": {
            "containers": [{"name": "app", "image": "nginx:latest"}],
            "nodeSelector": node_selector or {},
            "tolerations": [],
        },
    }


def _event(pod_name, reason, message, ns="default"):
    return {
        "type": "Warning",
        "reason": reason,
        "message": message,
        "involvedObject": {"kind": "Pod", "name": pod_name},
        "lastTimestamp": "2024-01-01T00:01:00Z",
        "metadata": {"namespace": ns},
    }


def _setup(mock_list, pods, events=None):
    data = {"pods": pods, "events": events or []}
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))


# ── No pending pods ───────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_no_pending_pods(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup(mock_list, pods=[_pod("running", phase="Running")])
    resp = client.get("/clusters/ns/cluster/scheduling-issues")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["pending_pods"] == []
    assert body["categories"] == {}


# ── Insufficient CPU ──────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_insufficient_cpu_detected(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    msg = "0/3 nodes are available: 3 Insufficient cpu."
    _setup(mock_list, pods=[_pod("cpu-hungry", sched_msg=msg)])
    resp = client.get("/clusters/ns/cluster/scheduling-issues")
    body = resp.json()
    assert body["total"] == 1
    pod = body["pending_pods"][0]
    assert pod["category"] == "insufficient_cpu"
    assert pod["message"] == msg
    assert body["categories"]["insufficient_cpu"] == 1


# ── Insufficient Memory ───────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_insufficient_memory_detected(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    msg = "0/3 nodes are available: 3 Insufficient memory."
    _setup(mock_list, pods=[_pod("mem-hog", sched_msg=msg)])
    resp = client.get("/clusters/ns/cluster/scheduling-issues")
    body = resp.json()
    assert body["pending_pods"][0]["category"] == "insufficient_memory"


# ── Taint toleration ─────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_taint_toleration_detected(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    msg = "0/2 nodes are available: 2 node(s) had untolerated taint {node-role.kubernetes.io/control-plane: }."
    _setup(mock_list, pods=[_pod("tainted", sched_msg=msg)])
    resp = client.get("/clusters/ns/cluster/scheduling-issues")
    body = resp.json()
    assert body["pending_pods"][0]["category"] == "taint_toleration"


# ── Image pull failure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_image_pull_detected(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    pod = _pod("bad-img", container_waitings=[("app", "ImagePullBackOff", "Back-off pulling image")])
    _setup(mock_list, pods=[pod])
    resp = client.get("/clusters/ns/cluster/scheduling-issues")
    body = resp.json()
    assert body["total"] == 1
    p = body["pending_pods"][0]
    assert p["category"] == "image_pull"
    assert len(p["image_issues"]) == 1
    assert p["image_issues"][0]["reason"] == "ImagePullBackOff"


# ── Event fallback ───────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_event_fallback_when_no_condition(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    pod = _pod("no-cond")  # no sched_msg
    ev = _event("no-cond", "FailedScheduling", "0/2 nodes are available: 2 Insufficient memory.")
    _setup(mock_list, pods=[pod], events=[ev])
    resp = client.get("/clusters/ns/cluster/scheduling-issues")
    body = resp.json()
    assert body["pending_pods"][0]["category"] == "insufficient_memory"


# ── Unknown category ─────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_unknown_category_for_no_message(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup(mock_list, pods=[_pod("mystery")])
    body = client.get("/clusters/ns/cluster/scheduling-issues").json()
    assert body["pending_pods"][0]["category"] == "unknown"


# ── image_pull sorted first ───────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_image_pull_sorted_first(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    cpu_pod = _pod("a-cpu", sched_msg="0/3 nodes: 3 Insufficient cpu.")
    img_pod = _pod("b-img", container_waitings=[("app", "ImagePullBackOff", "")])
    _setup(mock_list, pods=[cpu_pod, img_pod])
    body = client.get("/clusters/ns/cluster/scheduling-issues").json()
    assert body["pending_pods"][0]["category"] == "image_pull"
    assert body["pending_pods"][1]["category"] == "insufficient_cpu"


# ── Node selector included in response ───────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_node_selector_in_response(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    pod = _pod("sel-pod", node_selector={"disktype": "ssd", "region": "us-east"})
    _setup(mock_list, pods=[pod])
    body = client.get("/clusters/ns/cluster/scheduling-issues").json()
    assert body["pending_pods"][0]["node_selector"] == {"disktype": "ssd", "region": "us-east"}


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_response_structure(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    msg = "0/3 nodes are available: 3 Insufficient cpu."
    _setup(mock_list, pods=[_pod("p1", sched_msg=msg)])
    body = client.get("/clusters/ns/cluster/scheduling-issues").json()
    assert "pending_pods" in body
    assert "total" in body
    assert "categories" in body
    p = body["pending_pods"][0]
    for field in ["name", "namespace", "created_at", "category", "message", "images", "node_selector", "tolerations", "image_issues"]:
        assert field in p, f"missing field: {field}"
