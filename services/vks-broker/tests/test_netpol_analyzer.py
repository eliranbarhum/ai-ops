"""Tests for NetworkPolicy traffic analyzer (Loop 45)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module
from main import (
    _parse_label_selector, _labels_match_selector, _pod_selector_matches,
    _port_matches, _analyze_ingress, _analyze_egress,
)
from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _netpol(name: str, ns: str, pod_selector: dict, ingress: list = None,
            egress: list = None, policy_types: list = None) -> dict:
    spec = {"podSelector": pod_selector}
    if ingress is not None:
        spec["ingress"] = ingress
    if egress is not None:
        spec["egress"] = egress
    if policy_types:
        spec["policyTypes"] = policy_types
    return {"metadata": {"name": name, "namespace": ns}, "spec": spec}


def _ns(name: str, labels: dict = None) -> dict:
    return {"metadata": {"name": name, "labels": labels or {}}}


# ── Unit tests for helpers ────────────────────────────────────────────────────

def test_parse_label_selector():
    assert _parse_label_selector("app=web,env=prod") == {"app": "web", "env": "prod"}
    assert _parse_label_selector("") == {}
    assert _parse_label_selector("k=v") == {"k": "v"}


def test_labels_match_selector():
    assert _labels_match_selector({"app": "web", "env": "prod"}, {"app": "web"})
    assert not _labels_match_selector({"app": "web"}, {"app": "api"})
    assert _labels_match_selector({}, {})


def test_pod_selector_matches_empty_selects_all():
    # Empty podSelector matches all pods
    assert _pod_selector_matches({"app": "web"}, {})
    assert _pod_selector_matches({}, {})


def test_port_matches_empty_allows_all():
    assert _port_matches([], 80, "TCP")
    assert _port_matches([], 443, "TCP")


def test_port_matches_specific():
    ports = [{"port": 8080, "protocol": "TCP"}]
    assert _port_matches(ports, 8080, "TCP")
    assert not _port_matches(ports, 9090, "TCP")
    assert not _port_matches(ports, 8080, "UDP")


# ── Integration: no policies → allowed ───────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_analyze_no_policies_allowed(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "networkpolicies": [],
        "namespaces": [_ns("default")],
    }.get(kind, []))
    resp = client.get("/clusters/ns/cluster/netpol/analyze?src_ns=default&dst_ns=default&port=80")
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is True
    assert body["verdict"] == "allowed"


# ── Ingress policy blocks all from unlabeled source ───────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_analyze_ingress_blocks_when_no_matching_from(mock_list, mock_cluster):
    pol = _netpol("deny-all", "backend", {}, ingress=[], policy_types=["Ingress"])
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, ns=None, **kw: {
        "networkpolicies": [pol],
        "namespaces": [_ns("backend"), _ns("frontend")],
    }.get(kind, []))
    resp = client.get("/clusters/ns/cluster/netpol/analyze?src_ns=frontend&src_labels=app=web&dst_ns=backend&dst_labels=app=api&port=80")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ingress"]["allowed"] is False
    assert "deny-all" in body["ingress"]["blocked_by"]


# ── Ingress policy with matching from allows ──────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_analyze_ingress_allows_matching_pod_selector(mock_list, mock_cluster):
    pol = _netpol("allow-frontend", "backend",
                  pod_selector={"matchLabels": {"app": "api"}},
                  ingress=[{"from": [{"podSelector": {"matchLabels": {"app": "web"}}}],
                             "ports": [{"port": 8080, "protocol": "TCP"}]}],
                  policy_types=["Ingress"])
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, ns=None, **kw: {
        "networkpolicies": [pol],
        "namespaces": [_ns("backend")],
    }.get(kind, []))
    resp = client.get("/clusters/ns/cluster/netpol/analyze?src_ns=backend&src_labels=app=web&dst_ns=backend&dst_labels=app=api&port=8080")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ingress"]["allowed"] is True
    assert "allow-frontend" in body["ingress"]["allowed_by"]


# ── Verdict: blocked by both ──────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_analyze_blocked_by_both(mock_list, mock_cluster):
    ingress_pol = _netpol("deny-all-ingress", "default", {}, ingress=[], policy_types=["Ingress"])
    egress_pol = _netpol("deny-all-egress", "default", {}, egress=[], policy_types=["Egress"])
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, ns=None, **kw: {
        "networkpolicies": [ingress_pol, egress_pol],
        "namespaces": [_ns("default")],
    }.get(kind, []))
    resp = client.get("/clusters/ns/cluster/netpol/analyze?src_ns=default&src_labels=app=src&dst_ns=default&dst_labels=app=dst&port=80")
    body = resp.json()
    assert body["verdict"] == "blocked_by_both"
    assert body["allowed"] is False


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_analyze_response_structure(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(side_effect=lambda c, kind, *a, **kw: {
        "networkpolicies": [],
        "namespaces": [_ns("default")],
    }.get(kind, []))
    resp = client.get("/clusters/ns/cluster/netpol/analyze?src_ns=default&dst_ns=default&port=80")
    body = resp.json()
    assert "verdict" in body
    assert "allowed" in body
    assert "src" in body
    assert "dst" in body
    assert "ingress" in body
    assert "egress" in body
    assert "allowed_by" in body["ingress"]
    assert "blocked_by" in body["ingress"]
    assert "policy_count" in body["ingress"]
