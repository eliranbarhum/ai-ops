"""Tests for Namespace Label Compliance endpoint (Loop 60)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _ns(name, labels=None):
    return {"metadata": {"name": name, "labels": labels or {}}}


def _setup(mock_list, mock_cluster, namespaces):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=namespaces)


# ── Empty cluster ─────────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_empty_cluster(mock_list, mock_cluster):
    _setup(mock_list, mock_cluster, namespaces=[])
    body = client.get("/clusters/ns/cluster/namespace-labels").json()
    assert body["summary"]["total"] == 0
    assert body["namespaces"] == []


# ── Namespace without any labels is flagged ───────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_no_labels_flagged(mock_list, mock_cluster):
    _setup(mock_list, mock_cluster, namespaces=[_ns("myapp")])
    body = client.get("/clusters/ns/cluster/namespace-labels").json()
    r = body["namespaces"][0]
    assert "no_psa_label" in r["issues"]
    assert "no_team_label" in r["issues"]
    assert "no_env_label" in r["issues"]


# ── PSA label detected ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_psa_label_recognized(mock_list, mock_cluster):
    ns = _ns("secure", {"pod-security.kubernetes.io/enforce": "restricted"})
    _setup(mock_list, mock_cluster, namespaces=[ns])
    body = client.get("/clusters/ns/cluster/namespace-labels").json()
    r = body["namespaces"][0]
    assert r["psa_mode"] == "restricted"
    assert "no_psa_label" not in r["issues"]


# ── Team label variants all recognized ───────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_team_label_variants(mock_list, mock_cluster):
    for lbl in ["team", "owner", "app.kubernetes.io/part-of"]:
        ns = _ns("app", {lbl: "platform"})
        _setup(mock_list, mock_cluster, namespaces=[ns])
        body = client.get("/clusters/ns/cluster/namespace-labels").json()
        r = body["namespaces"][0]
        assert r["has_team_label"] is True, f"{lbl} not recognized"
        assert "no_team_label" not in r["issues"]


# ── Env label variants all recognized ────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_env_label_variants(mock_list, mock_cluster):
    for lbl in ["environment", "env", "stage"]:
        ns = _ns("app", {lbl: "production"})
        _setup(mock_list, mock_cluster, namespaces=[ns])
        body = client.get("/clusters/ns/cluster/namespace-labels").json()
        r = body["namespaces"][0]
        assert r["has_env_label"] is True, f"{lbl} not recognized"


# ── System namespaces excluded by default ────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_system_ns_excluded_default(mock_list, mock_cluster):
    nss = [_ns("kube-system"), _ns("kube-public"), _ns("myapp")]
    _setup(mock_list, mock_cluster, namespaces=nss)
    body = client.get("/clusters/ns/cluster/namespace-labels").json()
    names = [r["name"] for r in body["namespaces"]]
    assert "kube-system" not in names
    assert "myapp" in names
    assert body["summary"]["system_namespaces"] == 2


# ── include_system=true includes system namespaces ───────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_include_system(mock_list, mock_cluster):
    nss = [_ns("kube-system"), _ns("myapp")]
    _setup(mock_list, mock_cluster, namespaces=nss)
    body = client.get("/clusters/ns/cluster/namespace-labels?include_system=true").json()
    names = [r["name"] for r in body["namespaces"]]
    assert "kube-system" in names


# ── Custom required labels checked ───────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_custom_required_labels(mock_list, mock_cluster):
    ns = _ns("app", {"team": "platform", "env": "prod"})
    _setup(mock_list, mock_cluster, namespaces=[ns])
    body = client.get("/clusters/ns/cluster/namespace-labels?required=cost-center,compliance-level").json()
    r = body["namespaces"][0]
    assert "missing_custom_labels" in r["issues"]
    assert "cost-center" in r["missing_custom_labels"]
    assert "compliance-level" in r["missing_custom_labels"]


# ── Fully labeled namespace shows no issues ──────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_fully_labeled_no_issues(mock_list, mock_cluster):
    ns = _ns("prod", {
        "pod-security.kubernetes.io/enforce": "restricted",
        "team": "platform",
        "environment": "production",
    })
    _setup(mock_list, mock_cluster, namespaces=[ns])
    body = client.get("/clusters/ns/cluster/namespace-labels").json()
    r = body["namespaces"][0]
    assert r["issues"] == []
    assert body["summary"]["fully_labeled"] == 1


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_response_structure(mock_list, mock_cluster):
    _setup(mock_list, mock_cluster, namespaces=[_ns("app")])
    body = client.get("/clusters/ns/cluster/namespace-labels").json()
    assert "namespaces" in body and "summary" in body
    r = body["namespaces"][0]
    for field in ["name", "is_system", "labels", "label_count", "psa_mode",
                  "has_team_label", "has_env_label", "missing_custom_labels", "issues"]:
        assert field in r, f"missing field: {field}"
    s = body["summary"]
    for key in ["total", "system_namespaces", "no_psa_label", "no_team_label", "fully_labeled"]:
        assert key in s, f"missing summary key: {key}"
