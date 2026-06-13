"""Tests for Pod Detail Panel backend: events pod-filter + diagnose GET endpoint (Loop 1)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _event(name, pod_name="mypod", ev_type="Warning", reason="BackOff", message="Back-off restarting"):
    return {
        "metadata": {"name": name, "namespace": "default"},
        "type": ev_type,
        "reason": reason,
        "message": message,
        "count": 3,
        "lastTimestamp": "2026-06-12T10:00:00Z",
        "eventTime": None,
        "involvedObject": {"kind": "Pod", "name": pod_name},
        "source": {"component": "kubelet"},
    }


def _pod(name="mypod", ns="default", phase="Running"):
    return {
        "metadata": {"name": name, "namespace": ns},
        "spec": {"containers": [{"name": "app"}]},
        "status": {
            "phase": phase,
            "containerStatuses": [{"name": "app", "ready": True, "restartCount": 0, "state": {"running": {}}}],
            "conditions": [],
        },
    }


# ── Events endpoint: pod filter ───────────────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_list")
def test_events_returns_all_without_pod_filter(mock_list, mock_cluster):
    events = [_event("e1", "pod-a"), _event("e2", "pod-b")]
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=events)
    body = client.get("/clusters/ns/cluster/events?namespace=default").json()
    assert len(body["events"]) == 2


@patch("main._cluster")
@patch("main.kube_list")
def test_events_pod_filter_passes_field_selector(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_event("e1", "mypod")])
    client.get("/clusters/ns/cluster/events?namespace=default&pod=mypod")
    call_kwargs = mock_list.call_args
    assert call_kwargs.kwargs.get("field_selector") == "involvedObject.name=mypod"


@patch("main._cluster")
@patch("main.kube_list")
def test_events_no_pod_param_no_field_selector(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[])
    client.get("/clusters/ns/cluster/events?namespace=default")
    call_kwargs = mock_list.call_args
    assert call_kwargs.kwargs.get("field_selector") is None


@patch("main._cluster")
@patch("main.kube_list")
def test_events_severity_filter_still_works_with_pod(mock_list, mock_cluster):
    events = [_event("e1", ev_type="Warning"), _event("e2", ev_type="Normal")]
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=events)
    body = client.get("/clusters/ns/cluster/events?namespace=default&pod=mypod&severity=Warning").json()
    assert all(e["type"] == "Warning" for e in body["events"])


@patch("main._cluster")
@patch("main.kube_list")
def test_events_response_structure(mock_list, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_list.side_effect = AsyncMock(return_value=[_event("e1")])
    body = client.get("/clusters/ns/cluster/events?namespace=default&pod=mypod").json()
    assert "events" in body
    ev = body["events"][0]
    for field in ["type", "reason", "message", "count", "last_time", "source"]:
        assert field in ev, f"missing field: {field}"


# ── Diagnose endpoint: GET + mode param ──────────────────────────────────────

@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_list")
def test_diagnose_is_get_not_post(mock_list, mock_get, mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    mock_get.side_effect = AsyncMock(return_value=_pod())
    mock_list.side_effect = AsyncMock(return_value=[])
    # POST must now return 405
    r = client.post("/clusters/ns/cluster/pods/mypod/diagnose?namespace=default")
    assert r.status_code == 405


def _mock_cluster_with_log(log_text="app started"):
    log_resp = MagicMock()
    log_resp.status_code = 200
    log_resp.text = log_text
    cluster_client = MagicMock()
    cluster_client.get = AsyncMock(return_value=log_resp)
    return AsyncMock(return_value=cluster_client)


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_list")
@patch("main.LLM_GATEWAY_URL", "http://mock-llm")
@patch("httpx.AsyncClient")
def test_diagnose_get_returns_event_stream(mock_http, mock_list, mock_get, mock_cluster):
    mock_cluster.side_effect = _mock_cluster_with_log()
    mock_get.side_effect = AsyncMock(return_value=_pod())
    mock_list.side_effect = AsyncMock(return_value=[])
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"text": "Root cause: OOMKill"}
    mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
    mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
    r = client.get("/clusters/ns/cluster/pods/mypod/diagnose?namespace=default")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")


@patch("main._cluster")
@patch("main.kube_get")
@patch("main.kube_list")
@patch("main.LLM_GATEWAY_URL", "http://mock-llm")
@patch("httpx.AsyncClient")
def test_diagnose_teach_mode_accepted(mock_http, mock_list, mock_get, mock_cluster):
    mock_cluster.side_effect = _mock_cluster_with_log()
    mock_get.side_effect = AsyncMock(return_value=_pod())
    mock_list.side_effect = AsyncMock(return_value=[])
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"text": "Resource limits explanation"}
    mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
    mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
    r = client.get("/clusters/ns/cluster/pods/mypod/diagnose?namespace=default&mode=teach")
    assert r.status_code == 200
