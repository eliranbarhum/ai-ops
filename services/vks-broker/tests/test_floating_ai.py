"""Tests for Floating AI assistant endpoint (Loop 7)."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)


@patch("main.LLM_GATEWAY_URL", "http://mock-llm")
@patch("httpx.AsyncClient")
def test_ask_requires_question(mock_http):
    r = client.get("/ask")
    assert r.status_code == 422  # question is required


@patch("main.LLM_GATEWAY_URL", "http://mock-llm")
@patch("httpx.AsyncClient")
def test_ask_returns_event_stream(mock_http):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "Namespaces in K8s replace Projects in OpenShift."}
    mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
    mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
    r = client.get("/ask?question=What+is+a+namespace")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")


@patch("main.LLM_GATEWAY_URL", "http://mock-llm")
@patch("httpx.AsyncClient")
def test_ask_includes_context_params(mock_http):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "Your RBAC binding grants edit access."}
    mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
    mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
    r = client.get("/ask?question=Explain+this+binding&section=rbac&namespace=production&cluster_id=ns%2Fmy-cluster")
    assert r.status_code == 200
    # Verify the prompt sent to LLM included context
    call_kwargs = mock_http.return_value.__aenter__.return_value.post.call_args
    prompt = call_kwargs[1]["json"]["prompt"]
    assert "production" in prompt
    assert "rbac" in prompt
    assert "ns/my-cluster" in prompt


@patch("main.LLM_GATEWAY_URL", "http://mock-llm")
@patch("httpx.AsyncClient")
def test_ask_llm_error_returns_error_event(mock_http):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
    mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
    r = client.get("/ask?question=hello")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    body = r.text
    assert "error" in body


@patch("main.LLM_GATEWAY_URL", "http://mock-llm")
@patch("httpx.AsyncClient")
def test_ask_no_cluster_still_works(mock_http):
    """General K8s questions work without a cluster context."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "A pod is the smallest deployable unit in K8s."}
    mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
    mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
    r = client.get("/ask?question=What+is+a+pod")
    assert r.status_code == 200
    call_kwargs = mock_http.return_value.__aenter__.return_value.post.call_args
    prompt = call_kwargs[1]["json"]["prompt"]
    assert "general K8s context" in prompt
