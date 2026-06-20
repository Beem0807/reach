"""Session tokens for tenant admin logins (separate from platform admin tokens)."""
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

_EXPIRY_SECONDS = 8 * 3600


def _key() -> str:
    """Signing key for tenant session (login) tokens.

    Dedicated, freely-rotatable secret. Session tokens are short-lived (8h), so
    rotating ``SESSION_SIGNING_KEY`` is cheap - it just invalidates active
    sessions and users log in again. This is the opposite of ``TOKEN_PEPPER``,
    which hashes stored credentials and cannot be rotated without reissuing every
    token. Keeping them separate means session-key rotation never touches stored
    credentials.
    """
    return os.environ.get("SESSION_SIGNING_KEY", "localtest")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))


def _sign(msg: str) -> str:
    return _b64(hmac.new(_key().encode(), msg.encode(), hashlib.sha256).digest())


def create_tenant_token(user_id: str, tenant_id: str, role: str, username: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "tenant"}).encode())
    payload = _b64(json.dumps({
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "username": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + _EXPIRY_SECONDS,
    }).encode())
    signing = f"{header}.{payload}"
    return f"{signing}.{_sign(signing)}"


def verify_tenant_token(token: str) -> Optional[dict]:
    """Returns payload dict (with sub/tenant_id/role) or None if invalid/expired."""
    if not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        h, p, sig = parts
        # Reject platform admin tokens (header typ differs)
        header = json.loads(_b64d(h))
        if header.get("typ") != "tenant":
            return None
        signing = f"{h}.{p}"
        if not hmac.compare_digest(_sign(signing), sig):
            return None
        payload = json.loads(_b64d(p))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
