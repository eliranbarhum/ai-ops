"""Tests for cross-pod log search (Loop 53)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _pod(name, phase="Running"):
    return {"metadata": {"name": name, "namespace": "default"}, "status": {"phase": phase}}


def _log_resp(text: str):
    r = MagicMock()
    r.status_code = 200
    r.text = text
    return r


def _setup(mock_list, mock_cluster, pods, log_responses: dict):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=pods)

    http_client = MagicMock()
    http_client.get = AsyncMock(side_effect=lambda url, **kw: (
        _log_resp(log_responses.get(url.split("/")[-2], ""))
    ))
    mock_cluster.side_effect = AsyncMock(return_value=http_client)
    mock_list.side_effect = AsyncMock(return_value=pods)


# ── Query too short → 422 ─────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_short_query_rejected(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[])
    resp = client.get("/clusters/ns/cluster/log-search?q=x&namespace=default")
    assert resp.status_code == 422


# ── No namespace → 422 ───────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_no_namespace_rejected(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[])
    resp = client.get("/clusters/ns/cluster/log-search?q=error")
    assert resp.status_code == 422


# ── No matching lines ─────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_no_matches(mock_list, mock_cluster):
    pod = _pod("api")
    http = MagicMock()
    log_resp = MagicMock(); log_resp.status_code = 200; log_resp.text = "INFO: ok\nINFO: fine"
    http.get = AsyncMock(return_value=log_resp)
    mock_cluster.side_effect = AsyncMock(return_value=http)
    mock_list.side_effect = AsyncMock(return_value=[pod])
    body = client.get("/clusters/ns/cluster/log-search?q=error&namespace=default").json()
    assert body["total_matches"] == 0
    assert body["pods_with_matches"] == 0
    assert body["results"] == []


# ── Lines matching query returned ─────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_matches_returned(mock_list, mock_cluster):
    pod = _pod("api")
    http = MagicMock()
    logs = "INFO: ok\nERROR: something failed\nINFO: recovered\nERROR: again"
    log_resp = MagicMock(); log_resp.status_code = 200; log_resp.text = logs
    http.get = AsyncMock(return_value=log_resp)
    mock_cluster.side_effect = AsyncMock(return_value=http)
    mock_list.side_effect = AsyncMock(return_value=[pod])
    body = client.get("/clusters/ns/cluster/log-search?q=ERROR&namespace=default").json()
    assert body["total_matches"] == 2
    assert body["pods_with_matches"] == 1
    assert body["results"][0]["pod"] == "api"
    assert body["results"][0]["match_count"] == 2


# ── Case-insensitive search ───────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_case_insensitive_match(mock_list, mock_cluster):
    pod = _pod("api")
    http = MagicMock()
    log_resp = MagicMock(); log_resp.status_code = 200; log_resp.text = "error: db timeout"
    http.get = AsyncMock(return_value=log_resp)
    mock_cluster.side_effect = AsyncMock(return_value=http)
    mock_list.side_effect = AsyncMock(return_value=[pod])
    body = client.get("/clusters/ns/cluster/log-search?q=ERROR&namespace=default").json()
    assert body["total_matches"] == 1


# ── Multiple pods, sorted by match count ─────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_multiple_pods_sorted_by_match_count(mock_list, mock_cluster):
    pods = [_pod("a"), _pod("b"), _pod("c")]

    call_count = [-1]
    responses = [
        "error 1\nerror 2\nerror 3",  # pod a: 3 matches
        "ok\nfine",                   # pod b: 0 matches
        "error 1",                    # pod c: 1 match
    ]
    def get_side_effect(url, **kw):
        call_count[0] += 1
        r = MagicMock(); r.status_code = 200; r.text = responses[call_count[0] % 3]
        return r

    http = MagicMock(); http.get = AsyncMock(side_effect=get_side_effect)
    mock_cluster.side_effect = AsyncMock(return_value=http)
    mock_list.side_effect = AsyncMock(return_value=pods)
    body = client.get("/clusters/ns/cluster/log-search?q=error&namespace=default").json()
    assert body["pods_searched"] == 3
    assert body["pods_with_matches"] == 2
    assert body["results"][0]["match_count"] >= body["results"][1]["match_count"]


# ── Line number included in matches ──────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_line_number_in_match(mock_list, mock_cluster):
    pod = _pod("api")
    http = MagicMock()
    log_resp = MagicMock(); log_resp.status_code = 200
    log_resp.text = "line 1\nline 2\nerror on line 3\nline 4"
    http.get = AsyncMock(return_value=log_resp)
    mock_cluster.side_effect = AsyncMock(return_value=http)
    mock_list.side_effect = AsyncMock(return_value=[pod])
    body = client.get("/clusters/ns/cluster/log-search?q=error&namespace=default").json()
    m = body["results"][0]["matches"][0]
    assert m["line_no"] == 3
    assert "error on line 3" in m["line"]


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_response_structure(mock_list, mock_cluster):
    pod = _pod("api")
    http = MagicMock()
    log_resp = MagicMock(); log_resp.status_code = 200; log_resp.text = "exception found"
    http.get = AsyncMock(return_value=log_resp)
    mock_cluster.side_effect = AsyncMock(return_value=http)
    mock_list.side_effect = AsyncMock(return_value=[pod])
    body = client.get("/clusters/ns/cluster/log-search?q=exception&namespace=default").json()
    for field in ["query", "namespace", "pods_searched", "pods_with_matches", "total_matches", "results"]:
        assert field in body
    r = body["results"][0]
    for field in ["pod", "namespace", "match_count", "matches"]:
        assert field in r
    m = r["matches"][0]
    assert "line_no" in m and "line" in m


# ── Only Running/Pending pods included ───────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_completed_pods_excluded(mock_list, mock_cluster):
    pods = [_pod("run", "Running"), _pod("done", "Succeeded"), _pod("fail", "Failed")]
    http = MagicMock()
    log_resp = MagicMock(); log_resp.status_code = 200; log_resp.text = "error here"
    http.get = AsyncMock(return_value=log_resp)
    mock_cluster.side_effect = AsyncMock(return_value=http)
    mock_list.side_effect = AsyncMock(return_value=pods)
    body = client.get("/clusters/ns/cluster/log-search?q=error&namespace=default").json()
    assert body["pods_searched"] == 1  # only "run"
