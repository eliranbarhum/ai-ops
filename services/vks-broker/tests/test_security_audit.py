"""Tests for Pod Security Context Audit endpoint (Loop 59)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _pod(name, ns="default", phase="Running", containers=None,
         host_network=False, host_pid=False, host_ipc=False, pod_sc=None):
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {
            "hostNetwork": host_network,
            "hostPID": host_pid,
            "hostIPC": host_ipc,
            "securityContext": pod_sc or {},
            "containers": containers or [],
            "initContainers": [],
        },
        "status": {"phase": phase},
    }


def _container(name, privileged=False, allow_esc=None, run_as_user=None,
               run_as_non_root=None, read_only=False):
    sc: dict = {}
    if privileged:
        sc["privileged"] = True
    if allow_esc is not None:
        sc["allowPrivilegeEscalation"] = allow_esc
    if run_as_user is not None:
        sc["runAsUser"] = run_as_user
    if run_as_non_root is not None:
        sc["runAsNonRoot"] = run_as_non_root
    if read_only:
        sc["readOnlyRootFilesystem"] = True
    return {"name": name, "image": f"myimage/{name}:latest", "securityContext": sc}


def _setup(mock_list, mock_cluster, pods):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=pods)


# ── Empty cluster ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_empty_cluster(mock_list, mock_cluster):
    _setup(mock_list, mock_cluster, pods=[])
    body = client.get("/clusters/ns/cluster/security-audit").json()
    assert body["summary"]["total_pods"] == 0
    assert body["findings"] == []


# ── Privileged container detected ────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_privileged_container(mock_list, mock_cluster):
    pod = _pod("priv-pod", containers=[_container("app", privileged=True)])
    _setup(mock_list, mock_cluster, pods=[pod])
    body = client.get("/clusters/ns/cluster/security-audit").json()
    assert body["summary"]["privileged"] == 1
    f = body["findings"][0]
    assert "privileged" in f["risks"]


# ── hostNetwork pod flagged ───────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_host_network_flagged(mock_list, mock_cluster):
    pod = _pod("net-pod", host_network=True, containers=[_container("app")])
    _setup(mock_list, mock_cluster, pods=[pod])
    body = client.get("/clusters/ns/cluster/security-audit").json()
    assert body["summary"]["host_network"] == 1
    f = body["findings"][0]
    assert f["host_network"] is True
    assert "host_network" in f["risks"]


# ── Container with runAsUser=0 is run_as_root ─────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_run_as_root_uid0(mock_list, mock_cluster):
    pod = _pod("root-pod", containers=[_container("app", run_as_user=0)])
    _setup(mock_list, mock_cluster, pods=[pod])
    body = client.get("/clusters/ns/cluster/security-audit").json()
    assert body["summary"]["run_as_root"] >= 1
    f = body["findings"][0]
    assert "run_as_root" in f["risks"]


# ── Container with runAsNonRoot=true is safe ─────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_run_as_non_root_safe(mock_list, mock_cluster):
    pod = _pod("safe-pod", containers=[
        _container("app", run_as_non_root=True, run_as_user=1000, read_only=True, allow_esc=False)
    ])
    _setup(mock_list, mock_cluster, pods=[pod])
    body = client.get("/clusters/ns/cluster/security-audit").json()
    # No run_as_root risk for this container
    for f in body["findings"]:
        for cd in f["containers"]:
            assert "run_as_root" not in cd["risks"]


# ── Completed pods excluded ───────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_succeeded_pods_excluded(mock_list, mock_cluster):
    pod = _pod("done-job", phase="Succeeded", containers=[_container("app", privileged=True)])
    _setup(mock_list, mock_cluster, pods=[pod])
    body = client.get("/clusters/ns/cluster/security-audit").json()
    assert body["summary"]["total_pods"] == 0
    assert body["findings"] == []


# ── Findings sorted by risk_score ────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_sorted_by_risk_score(mock_list, mock_cluster):
    low_risk  = _pod("low", containers=[_container("app")])
    high_risk = _pod("high", host_network=True, host_pid=True, containers=[
        _container("app", privileged=True, run_as_user=0)
    ])
    _setup(mock_list, mock_cluster, pods=[low_risk, high_risk])
    body = client.get("/clusters/ns/cluster/security-audit").json()
    if len(body["findings"]) >= 2:
        assert body["findings"][0]["risk_score"] >= body["findings"][1]["risk_score"]


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_response_structure(mock_list, mock_cluster):
    pod = _pod("test", containers=[_container("app", privileged=True)])
    _setup(mock_list, mock_cluster, pods=[pod])
    body = client.get("/clusters/ns/cluster/security-audit").json()
    assert "findings" in body and "summary" in body
    f = body["findings"][0]
    for field in ["name", "namespace", "phase", "host_network", "host_pid",
                  "host_ipc", "containers", "risks", "risk_score"]:
        assert field in f, f"missing field: {field}"
    s = body["summary"]
    for key in ["total_pods", "flagged_pods", "privileged", "run_as_root",
                "allow_escalation", "host_network", "host_pid", "host_ipc"]:
        assert key in s, f"missing summary key: {key}"


# ── no_read_only_root_fs counted ─────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_read_only_root_counted(mock_list, mock_cluster):
    pod = _pod("rw-pod", containers=[
        _container("app", run_as_non_root=True, run_as_user=1000, allow_esc=False, read_only=False)
    ])
    _setup(mock_list, mock_cluster, pods=[pod])
    body = client.get("/clusters/ns/cluster/security-audit").json()
    assert body["summary"]["no_read_only_root"] >= 1
