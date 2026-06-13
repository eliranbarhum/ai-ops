"""
AES-128 symmetric encryption via Fernet (cryptography library).
Key lifecycle:
  1. If ENCRYPTION_KEY env var is set → use it (K8s secret / Docker secret).
  2. If /data/mco.key exists → load from disk (auto-generated on first run).
  3. Otherwise → generate, persist to /data/mco.key, log warning.

The key file must be on a persistent volume. The encrypted config is at /data/mco-config.enc.
"""

import os
import logging
from pathlib import Path
from cryptography.fernet import Fernet

logger = logging.getLogger("config-store.crypto")

KEY_ENV = "ENCRYPTION_KEY"
KEY_FILE = Path("/data/mco.key")
CONFIG_FILE = Path("/data/mco-config.enc")


def _load_or_create_key() -> bytes:
    env_key = os.getenv(KEY_ENV)
    if env_key:
        return env_key.encode()

    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()

    key = Fernet.generate_key()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key)
    KEY_FILE.chmod(0o600)
    logger.warning(
        "ENCRYPTION_KEY not set — generated a new key at %s. "
        "Back this file up or set ENCRYPTION_KEY in your environment.",
        KEY_FILE,
    )
    return key


_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return get_fernet().decrypt(token.encode()).decode()
