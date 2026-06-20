import base64
import hashlib
import hmac
import json
import os
import time

_EXPIRY_SECONDS = 8 * 3600  # 8 hours


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))


def _sign(key: str, msg: str) -> str:
    return _b64url(hmac.new(key.encode(), msg.encode(), hashlib.sha256).digest())


def create_session_token() -> str:
    password = os.environ.get("ADMIN_PASSWORD", "")
    header  = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"sub": "admin", "iat": int(time.time()), "exp": int(time.time()) + _EXPIRY_SECONDS}).encode())
    signing_input = f"{header}.{payload}"
    return f"{signing_input}.{_sign(password, signing_input)}"


def verify_session_token(token: str) -> bool:
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password or not token:
        return False
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False
        signing_input = f"{parts[0]}.{parts[1]}"
        if not hmac.compare_digest(_sign(password, signing_input), parts[2]):
            return False
        payload = json.loads(_b64url_decode(parts[1]))
        return payload.get("exp", 0) > time.time()
    except Exception:
        return False
