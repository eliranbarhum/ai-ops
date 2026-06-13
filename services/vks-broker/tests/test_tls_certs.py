"""Tests for TLS certificate expiry scanner (Loop 46)."""
import pytest, base64
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(os.environ, {"REDIS_URL": "", "CONFIRM_SECRET": "test-secret"}):
    with patch("broker.load_imported_from_secret", new=AsyncMock()):
        import main as app_module

from main import _parse_tls_cert
from fastapi.testclient import TestClient

client = TestClient(app_module.app)


def _make_cert_pem_b64(cn: str, sans: list, days: int) -> str:
    """days > 0 = valid for N days from now; days < 0 = expired N days ago."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    if days >= 0:
        not_before = now - timedelta(days=1)
        not_after = now + timedelta(days=days)
    else:
        # Already expired: set both dates in the past
        not_before = now - timedelta(days=abs(days) + 30)
        not_after = now - timedelta(days=abs(days))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    san_list = [x509.DNSName(s) for s in sans]
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    return base64.b64encode(pem).decode()


def _tls_secret(name: str, ns: str, pem_b64: str) -> dict:
    return {
        "metadata": {"name": name, "namespace": ns},
        "type": "kubernetes.io/tls",
        "data": {"tls.crt": pem_b64, "tls.key": ""},
    }


# ── Unit: _parse_tls_cert ─────────────────────────────────────────────────────

def test_parse_cert_ok():
    pem_b64 = _make_cert_pem_b64("my.example.com", ["my.example.com", "alt.example.com"], 90)
    pem = base64.b64decode(pem_b64)
    result = _parse_tls_cert(pem)
    assert result is not None
    assert result["cn"] == "my.example.com"
    assert "my.example.com" in result["sans"]
    assert "alt.example.com" in result["sans"]
    assert result["days_remaining"] >= 89


def test_parse_cert_expiry_days():
    pem_b64 = _make_cert_pem_b64("test.com", ["test.com"], 5)
    pem = base64.b64decode(pem_b64)
    result = _parse_tls_cert(pem)
    assert result["days_remaining"] in (4, 5)  # allow 1-day tolerance


def test_parse_cert_invalid_returns_none():
    result = _parse_tls_cert(b"not a cert")
    assert result is None


# ── Integration ────────────────────────────────────────────────────────────────

def _mock_cluster_with_secrets(mock_cluster, secrets: list) -> None:
    """Set up mock cluster client that returns given secrets from client.get()."""
    import json as _json
    http = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"items": secrets})
    http.get = AsyncMock(return_value=resp)
    mock_cluster.side_effect = AsyncMock(return_value=http)


@patch("main._cluster")
def test_tls_certs_skips_non_tls_secrets(mock_cluster):
    _mock_cluster_with_secrets(mock_cluster, [
        {"metadata": {"name": "opaque-sec", "namespace": "default"}, "type": "Opaque", "data": {}},
    ])
    resp = client.get("/clusters/ns/cluster/tls-certs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@patch("main._cluster")
def test_tls_certs_detects_valid_cert(mock_cluster):
    pem_b64 = _make_cert_pem_b64("app.example.com", ["app.example.com"], 60)
    _mock_cluster_with_secrets(mock_cluster, [_tls_secret("app-tls", "default", pem_b64)])
    resp = client.get("/clusters/ns/cluster/tls-certs")
    body = resp.json()
    assert body["total"] == 1
    cert = body["certs"][0]
    assert cert["cn"] == "app.example.com"
    assert cert["status"] == "ok"
    assert cert["days_remaining"] >= 55


@patch("main._cluster")
def test_tls_certs_status_warning(mock_cluster):
    pem_b64 = _make_cert_pem_b64("expiring.com", ["expiring.com"], 10)
    _mock_cluster_with_secrets(mock_cluster, [_tls_secret("expiring-tls", "default", pem_b64)])
    resp = client.get("/clusters/ns/cluster/tls-certs?days_warning=30")
    body = resp.json()
    assert body["certs"][0]["status"] == "warning"
    assert body["warning"] == 1


@patch("main._cluster")
def test_tls_certs_status_expired(mock_cluster):
    pem_b64 = _make_cert_pem_b64("old.com", ["old.com"], -10)
    _mock_cluster_with_secrets(mock_cluster, [_tls_secret("old-tls", "default", pem_b64)])
    resp = client.get("/clusters/ns/cluster/tls-certs")
    body = resp.json()
    assert body["certs"][0]["status"] == "expired"
    assert body["expired"] == 1


@patch("main._cluster")
def test_tls_certs_sorted_expired_first(mock_cluster):
    ok_pem = _make_cert_pem_b64("ok.com", ["ok.com"], 365)
    warn_pem = _make_cert_pem_b64("warn.com", ["warn.com"], 15)
    exp_pem = _make_cert_pem_b64("exp.com", ["exp.com"], -5)
    _mock_cluster_with_secrets(mock_cluster, [
        _tls_secret("ok-tls", "default", ok_pem),
        _tls_secret("warn-tls", "default", warn_pem),
        _tls_secret("exp-tls", "default", exp_pem),
    ])
    resp = client.get("/clusters/ns/cluster/tls-certs?days_warning=30")
    body = resp.json()
    statuses = [c["status"] for c in body["certs"]]
    assert statuses[0] == "expired"
    assert statuses[1] == "warning"
    assert statuses[2] == "ok"


@patch("main._cluster")
def test_tls_certs_summary_counts(mock_cluster):
    ok_pem = _make_cert_pem_b64("ok.com", ["ok.com"], 365)
    warn_pem = _make_cert_pem_b64("warn.com", ["warn.com"], 15)
    exp_pem = _make_cert_pem_b64("exp.com", ["exp.com"], -5)
    _mock_cluster_with_secrets(mock_cluster, [
        _tls_secret("ok-tls", "default", ok_pem),
        _tls_secret("warn-tls", "default", warn_pem),
        _tls_secret("exp-tls", "default", exp_pem),
    ])
    resp = client.get("/clusters/ns/cluster/tls-certs?days_warning=30")
    body = resp.json()
    assert body["expired"] == 1
    assert body["warning"] == 1
    assert body["ok"] == 1
    assert body["total"] == 3
