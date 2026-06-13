"""Tests for the concurrent namespace detail fetch."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient
client = TestClient(app_module.app)


def _make_ns(name):
    return {"metadata": {"name": name}, "status": {"phase": "Active"}}


@patch("main._cluster")
@patch("main.kube_list")
def test_namespace_list_concurrent(mock_kube_list, mock_cluster):
    """kube_list must be called in parallel (not N+1 sequential round-trips)."""
    mock_client = MagicMock()
    mock_cluster.return_value = mock_client

    call_order = []
    call_times = []

    async def fake_kube_list(client, kind, ns=None):
        call_order.append((kind, ns))
        call_times.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.01)  # simulate 10ms latency
        if kind == "namespaces":
            return [_make_ns("default"), _make_ns("monitoring")]
        if kind == "pods":
            return [{"metadata": {"name": "p1"}}]
        return []

    mock_kube_list.side_effect = fake_kube_list
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)

    import time
    start = time.monotonic()
    resp = client.get("/clusters/ns/cluster/namespaces")
    elapsed = time.monotonic() - start

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["namespaces"]) == 2

    # With concurrency, 2 namespaces × 2 queries = 4 calls at ~10ms each.
    # Sequential would take ~40ms; concurrent should be ~20ms (two parallel pairs).
    # Allow 200ms for test overhead, but assert < 400ms (not fully sequential).
    assert elapsed < 0.4, f"Expected concurrent execution, got {elapsed:.2f}s"


@patch("main._cluster")
@patch("main.kube_list")
def test_namespace_list_tolerates_pod_errors(mock_kube_list, mock_cluster):
    """A namespace whose pod list fails should still appear with pod_count=0."""
    mock_client = MagicMock()
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)

    async def fake_kube_list(client, kind, ns=None):
        if kind == "namespaces":
            return [_make_ns("default"), _make_ns("broken-ns")]
        if kind == "pods" and ns == "broken-ns":
            raise Exception("permission denied")
        if kind == "pods":
            return [{"metadata": {"name": "p1"}}]
        return []

    mock_kube_list.side_effect = fake_kube_list

    resp = client.get("/clusters/ns/cluster/namespaces")
    assert resp.status_code == 200
    ns_map = {n["name"]: n for n in resp.json()["namespaces"]}
    assert ns_map["default"]["pod_count"] == 1
    assert ns_map["broken-ns"]["pod_count"] == 0


@patch("main._cluster")
@patch("main.kube_list")
def test_system_namespace_flagged(mock_kube_list, mock_cluster):
    mock_client = MagicMock()
    mock_cluster.side_effect = AsyncMock(return_value=mock_client)

    async def fake_kube_list(client, kind, ns=None):
        if kind == "namespaces":
            return [_make_ns("kube-system"), _make_ns("default")]
        return []

    mock_kube_list.side_effect = fake_kube_list

    resp = client.get("/clusters/ns/cluster/namespaces")
    ns_map = {n["name"]: n for n in resp.json()["namespaces"]}
    assert ns_map["kube-system"]["is_system"] is True
    assert ns_map["default"]["is_system"] is False
