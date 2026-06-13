"""Tests for RBAC risk auditor (Loop 51)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _crole(name, rules):
    return {"metadata": {"name": name}, "rules": rules}


def _crb(name, role_name, subjects):
    return {
        "metadata": {"name": name},
        "roleRef": {"kind": "ClusterRole", "name": role_name},
        "subjects": subjects,
    }


def _rb(name, ns, role_name, role_kind, subjects):
    return {
        "metadata": {"name": name, "namespace": ns},
        "roleRef": {"kind": role_kind, "name": role_name},
        "subjects": subjects,
    }


def _subj(kind="ServiceAccount", name="app", ns="default"):
    return {"kind": kind, "name": name, "namespace": ns}


def _setup(mock_list, croles=None, crbs=None, roles=None, rolebindings=None):
    data = {
        "clusterroles": croles or [],
        "clusterrolebindings": crbs or [],
        "roles": roles or [],
        "rolebindings": rolebindings or [],
    }
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: data.get(kind, []))


# ── No risks ──────────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_no_risks_empty_cluster(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup(mock_list)
    resp = client.get("/clusters/ns/cluster/rbac/risks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total"] == 0
    assert body["risks"] == []


# ── cluster-admin CRB → critical ──────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_cluster_admin_crb_is_critical(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    _setup(mock_list,
        crbs=[_crb("admin-binding", "cluster-admin", [_subj("User", "dev-user", "")])],
    )
    body = client.get("/clusters/ns/cluster/rbac/risks").json()
    assert body["summary"]["critical"] >= 1
    risk = next(r for r in body["risks"] if r["type"] == "cluster_admin_grant")
    assert risk["severity"] == "critical"
    assert risk["subject_name"] == "dev-user"


# ── system:masters group → critical ──────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_system_masters_is_critical(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    crole = _crole("view", [{"verbs": ["get"], "resources": ["pods"], "apiGroups": [""]}])
    crb = _crb("masters-binding", "view", [{"kind": "Group", "name": "system:masters", "namespace": ""}])
    _setup(mock_list, croles=[crole], crbs=[crb])
    body = client.get("/clusters/ns/cluster/rbac/risks").json()
    assert any(r["type"] == "system_masters_grant" for r in body["risks"])
    assert body["summary"]["critical"] >= 1


# ── Wildcard rules on ClusterRole → high/critical ────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_wildcard_all_is_critical(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    crole = _crole("super-role", [{"verbs": ["*"], "resources": ["*"], "apiGroups": ["*"]}])
    crb = _crb("super-binding", "super-role", [_subj("ServiceAccount", "app", "prod")])
    _setup(mock_list, croles=[crole], crbs=[crb])
    body = client.get("/clusters/ns/cluster/rbac/risks").json()
    risk = next(r for r in body["risks"] if r["type"] == "risky_clusterrole")
    assert risk["severity"] == "critical"
    assert any(f["type"] == "wildcard_all" for f in risk["findings"])


# ── Secrets write access → critical ──────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_secrets_write_is_critical(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    crole = _crole("secret-reader", [{"verbs": ["get", "create"], "resources": ["secrets"], "apiGroups": [""]}])
    crb = _crb("sec-binding", "secret-reader", [_subj("ServiceAccount", "app", "ns")])
    _setup(mock_list, croles=[crole], crbs=[crb])
    body = client.get("/clusters/ns/cluster/rbac/risks").json()
    risk = next(r for r in body["risks"] if r["type"] == "risky_clusterrole")
    assert risk["severity"] == "critical"
    assert any(f["type"] == "secrets_write" for f in risk["findings"])


# ── Pod exec → high ───────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_pod_exec_is_high(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    crole = _crole("exec-role", [{"verbs": ["create"], "resources": ["pods/exec"], "apiGroups": [""]}])
    crb = _crb("exec-binding", "exec-role", [_subj("ServiceAccount", "ci-runner", "ci")])
    _setup(mock_list, croles=[crole], crbs=[crb])
    body = client.get("/clusters/ns/cluster/rbac/risks").json()
    risk = next(r for r in body["risks"] if r["type"] == "risky_clusterrole")
    assert risk["severity"] == "high"
    assert any(f["type"] == "pod_exec" for f in risk["findings"])


# ── RoleBinding with risky ClusterRole ref ────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_rolebinding_risky_clusterrole(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    crole = _crole("ns-admin", [{"verbs": ["*"], "resources": ["*"], "apiGroups": [""]}])
    rb = _rb("rb-1", "default", "ns-admin", "ClusterRole", [_subj("ServiceAccount", "app", "default")])
    _setup(mock_list, croles=[crole], rolebindings=[rb])
    body = client.get("/clusters/ns/cluster/rbac/risks").json()
    assert body["summary"]["total"] >= 1
    risk = body["risks"][0]
    assert risk["binding_kind"] == "RoleBinding"
    assert risk["namespace"] == "default"


# ── Sorted critical first ─────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_risks_sorted_critical_first(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    # high-only role
    crole_h = _crole("exec-role", [{"verbs": ["create"], "resources": ["pods/exec"], "apiGroups": [""]}])
    crb_h = _crb("exec-binding", "exec-role", [_subj("ServiceAccount", "ci", "ci")])
    # cluster-admin (critical)
    crb_c = _crb("admin-binding", "cluster-admin", [_subj("User", "admin", "")])
    _setup(mock_list, croles=[crole_h], crbs=[crb_h, crb_c])
    body = client.get("/clusters/ns/cluster/rbac/risks").json()
    risks = body["risks"]
    assert risks[0]["severity"] == "critical"


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_response_structure(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    crb = _crb("admin-binding", "cluster-admin", [_subj("User", "alice", "")])
    _setup(mock_list, crbs=[crb])
    body = client.get("/clusters/ns/cluster/rbac/risks").json()
    assert "risks" in body
    assert "summary" in body
    s = body["summary"]
    for key in ["total", "critical", "high", "medium"]:
        assert key in s
    r = body["risks"][0]
    for field in ["severity", "type", "binding", "binding_kind", "namespace", "role", "subject_kind", "subject_name", "findings"]:
        assert field in r, f"missing field: {field}"


# ── Safe role produces no risk ────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_safe_role_no_risk(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    crole = _crole("pod-reader", [{"verbs": ["get", "list", "watch"], "resources": ["pods"], "apiGroups": [""]}])
    crb = _crb("pod-reader-binding", "pod-reader", [_subj("ServiceAccount", "monitor", "monitoring")])
    _setup(mock_list, croles=[crole], crbs=[crb])
    body = client.get("/clusters/ns/cluster/rbac/risks").json()
    assert body["summary"]["total"] == 0
