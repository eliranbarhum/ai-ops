"""Tests for cross-namespace resource search endpoint (Loop 43)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _item(name: str, namespace: str = "default", kind_hint: str = "",
          labels: dict = None, phase: str = "") -> dict:
    item = {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "creationTimestamp": "2024-01-01T00:00:00Z",
            "labels": labels or {},
        },
        "spec": {},
        "status": {},
    }
    if phase:
        item["status"]["phase"] = phase
    return item


def _list_resp(items: list) -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"items": items}
    return m


# ── Basic query validation ────────────────────────────────────────────────────

@patch("main._cluster")
def test_search_empty_query_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.get("/clusters/ns/cluster/search?q=")
    assert resp.status_code == 400


@patch("main._cluster")
def test_search_short_query_400(mock_cluster):
    mock_cluster.side_effect = AsyncMock(return_value=MagicMock())
    resp = client.get("/clusters/ns/cluster/search?q=a")
    assert resp.status_code == 400


# ── Name match ────────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_search_finds_by_name(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=lambda url, **kw: _list_resp([
        _item("my-nginx-deployment", "default"),
        _item("other-app", "default"),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/search?q=nginx&kinds=deployments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["results"][0]["name"] == "my-nginx-deployment"
    assert body["results"][0]["kind"] == "Deployment"


# ── Namespace match ───────────────────────────────────────────────────────────

@patch("main._cluster")
def test_search_finds_by_namespace(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=lambda url, **kw: _list_resp([
        _item("app-1", "prod-ns"),
        _item("app-2", "dev-ns"),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/search?q=prod&kinds=pods")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["results"][0]["namespace"] == "prod-ns"


# ── Label match ───────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_search_finds_by_label(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=lambda url, **kw: _list_resp([
        _item("app-a", "default", labels={"env": "staging", "app": "frontend"}),
        _item("app-b", "default", labels={"env": "production"}),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/search?q=staging&kinds=pods")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["results"][0]["name"] == "app-a"


# ── Response structure ────────────────────────────────────────────────────────

@patch("main._cluster")
def test_search_result_structure(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=lambda url, **kw: _list_resp([
        _item("test-app", "staging", phase="Running"),
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/search?q=test&kinds=pods")
    assert resp.status_code == 200
    body = resp.json()
    r = body["results"][0]
    assert "kind" in r
    assert "name" in r
    assert "namespace" in r
    assert "labels" in r
    assert "created_at" in r
    assert "status" in r


# ── Kinds filter ──────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_search_kinds_filter(mock_cluster):
    call_urls = []
    def _track(url, **kw):
        call_urls.append(url)
        return _list_resp([])
    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=_track)
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/search?q=test&kinds=pods,services")
    assert resp.status_code == 200
    # Only 2 kinds fetched
    assert len(call_urls) == 2
    assert "pods" in body_kinds(resp) or "services" in body_kinds(resp)


def body_kinds(resp):
    return resp.json().get("kinds_searched", [])


# ── Default kinds ─────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_search_default_kinds_used(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_list_resp([]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/search?q=test")
    assert resp.status_code == 200
    kinds = resp.json()["kinds_searched"]
    assert len(kinds) > 0


# ── Limit ─────────────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_search_respects_limit(mock_cluster):
    mock_http = MagicMock()
    # Return 20 items
    mock_http.get = AsyncMock(side_effect=lambda url, **kw: _list_resp([
        _item(f"test-item-{i}", "default") for i in range(20)
    ]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/search?q=test&kinds=pods&limit=5")
    assert resp.status_code == 200
    assert resp.json()["total"] <= 5


# ── No results ────────────────────────────────────────────────────────────────

@patch("main._cluster")
def test_search_no_match_returns_empty(mock_cluster):
    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=_list_resp([_item("completely-unrelated", "default")]))
    mock_cluster.side_effect = AsyncMock(return_value=mock_http)
    resp = client.get("/clusters/ns/cluster/search?q=xyznotfound&kinds=pods")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
