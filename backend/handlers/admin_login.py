"""Platform admin login - verifies ADMIN_PASSWORD and issues a session token."""
import hmac
import json
import logging
import os

import shared.audit as audit
from shared.admin_auth import create_session_token
from shared.response import _err, _ok

logger = logging.getLogger()


def handle_admin_login(body: dict, ip: str = "") -> dict:
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_password:
        return _err("ADMIN_PASSWORD not configured", 500)
    password = (body.get("password") or "")
    if not password or not hmac.compare_digest(password, admin_password):
        # Failed privileged auth attempt by an unknown party - log for compliance.
        audit.write(
            "admin.login_failed",
            actor_id="unknown",
            actor_name="unknown",
            ip_address=ip or None,
        )
        return _err("invalid credentials", 401)
    audit.write("admin.login", ip_address=ip or None)
    return _ok({"token": create_session_token()})


def _ip(event: dict) -> str:
    ctx = event.get("requestContext") or {}
    return (ctx.get("http") or ctx.get("identity") or {}).get("sourceIp", "")


def admin_login_handler(event, context):
    logger.info("POST /admin/login")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_admin_login(body, _ip(event))
