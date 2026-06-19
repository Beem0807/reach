import base64
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from shared.response import _err, _iso, _ok
from shared.store import agents_repo, approvals_repo

_DURATION_SECONDS = {"1h": 3600, "8h": 28800, "24h": 86400, "7d": 604800}


def _parse_expires_at(duration: str) -> Tuple[bool, Optional[str]]:
    """Returns (ok, expires_at_iso_or_None). expires_at=None means permanent."""
    if not duration or duration == "permanent":
        return True, None
    if duration == "now":
        return True, datetime.now(tz=timezone.utc).isoformat()
    if duration in _DURATION_SECONDS:
        secs = _DURATION_SECONDS[duration]
    else:
        m = re.fullmatch(r"(\d+)(h|d)", duration)
        if not m:
            return False, None
        n, unit = int(m.group(1)), m.group(2)
        secs = n * 3600 if unit == "h" else n * 86400
    expires = datetime.now(tz=timezone.utc) + timedelta(seconds=secs)
    return True, expires.isoformat()

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]


def _verify_admin(raw: str) -> bool:
    import hmac
    return hmac.compare_digest(raw, ADMIN_TOKEN)


def _decode_cursor(s: str) -> Optional[str]:
    try:
        return base64.urlsafe_b64decode(s.encode()).decode()
    except Exception:
        return None


def _encode_cursor(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def handle_list_approvals(query: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    tenant_id = query.get("tenant_id", "").strip() or None
    agent_id = query.get("agent_id", "").strip() or None
    status = query.get("status", "").strip() or None
    try:
        limit = max(1, min(int(query.get("limit", 20)), 100))
    except (ValueError, TypeError):
        limit = 20
    decoded_cursor = _decode_cursor(query["cursor"]) if query.get("cursor") else None
    if tenant_id:
        approvals = approvals_repo.list_by_tenant(tenant_id, agent_id=agent_id, status=status, limit=limit, cursor=decoded_cursor)
    elif agent_id:
        approvals = approvals_repo.list_by_agent(agent_id, status=status, limit=limit, cursor=decoded_cursor)
    else:
        approvals = []
    result: dict = {"approvals": approvals}
    if len(approvals) == limit and approvals:
        result["next_cursor"] = _encode_cursor(approvals[-1]["created_at"])
    return _ok(result)


def handle_review_approval(approval_id: str, action: str, raw_token: str, body: Optional[dict] = None) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    if action not in ("approve", "deny"):
        return _err("action must be approve or deny", 400)
    approval = approvals_repo.get(approval_id)
    if not approval:
        return _err("approval not found", 404)
    current_status = approval.get("status")
    if current_status in ("denied", "expired"):
        return _err(f"{current_status} approvals cannot be updated", 409)
    if current_status == "approved" and action == "deny":
        return _err("approved approvals cannot be denied - use duration=now to instantly expire instead", 409)
    new_status = "approved" if action == "approve" else "denied"
    reviewed_at = _iso()
    expires_at: Optional[str] = None
    if action == "approve":
        duration = (body or {}).get("duration", "permanent")
        if duration == "now" and current_status == "pending":
            return _err("duration=now is not valid for initial approval; use 1h, 8h, 24h, 7d, permanent, or Nh/Nd", 400)
        ok, expires_at = _parse_expires_at(duration)
        if not ok:
            return _err(f"invalid duration '{duration}'; use 1h, 8h, 24h, 7d, permanent, now, or Nh/Nd", 400)
        if duration == "now":
            new_status = "expired"
    approvals_repo.update_status(approval_id, new_status, reviewed_at, "admin", expires_at=expires_at)
    updated = {**approval, "status": new_status, "reviewed_at": reviewed_at, "reviewed_by": "admin", "expires_at": expires_at}
    logger.info("Approval %s %s by admin (expires_at=%s)", approval_id, new_status, expires_at)
    return _ok(updated)


# ---------------------------------------------------------------------------
# Lambda handlers
# ---------------------------------------------------------------------------

def _token_from_event(event: dict) -> str:
    auth = (event.get("headers") or {}).get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def handle_pre_approve_command(body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    agent_id = (body or {}).get("agent_id", "").strip()
    if not agent_id:
        return _err("agent_id is required", 400)

    single_command = (body or {}).get("command", "").strip()
    command_list = (body or {}).get("commands")
    if command_list is not None:
        if not isinstance(command_list, list) or not command_list:
            return _err("commands must be a non-empty list", 400)
        commands = [c.strip() for c in command_list if isinstance(c, str) and c.strip()]
        if not commands:
            return _err("commands must contain at least one non-empty string", 400)
        bulk = True
    elif single_command:
        commands = [single_command]
        bulk = False
    else:
        return _err("command or commands is required", 400)

    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)

    duration = (body or {}).get("duration")
    if duration == "now":
        return _err("duration=now is not valid for pre-approve; use 1h, 8h, 24h, 7d, permanent, or Nh/Nd", 400)
    ok, expires_at = _parse_expires_at(duration)
    if not ok:
        return _err(f"invalid duration '{duration}'; use 1h, 8h, 24h, 7d, permanent, or Nh/Nd", 400)

    active_commands = {a["command"] for a in approvals_repo.list_by_agent(agent_id, status="approved")}
    now = _iso()
    created = []
    skipped = []

    for command in commands:
        if command in active_commands:
            skipped.append({"command": command, "reason": "already_approved"})
            continue
        approval = {
            "approval_id": "appr_" + secrets.token_urlsafe(12),
            "tenant_id": agent["tenant_id"],
            "agent_id": agent_id,
            "command": command,
            "requested_by": "admin",
            "requester_name": "admin",
            "job_id": None,
            "status": "approved",
            "created_at": now,
            "reviewed_at": now,
            "reviewed_by": "admin",
            "expires_at": expires_at,
        }
        approvals_repo.create(approval)
        created.append(approval)

    logger.info("Admin pre-approved %d commands for agent %s (%d skipped)", len(created), agent_id, len(skipped))

    if bulk:
        return _ok({"created": created, "skipped": skipped})
    # Single command: backward-compatible response
    if skipped:
        return _err("command already has an active approval for this agent", 409)
    return _ok(created[0])


def pre_approve_command_handler(event, context):
    logger.info("POST /admin/approvals")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    raw_body = event.get("body") or "{}"
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        body = {}
    return handle_pre_approve_command(body, token)


def handle_delete_approval(approval_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    approval = approvals_repo.get(approval_id)
    if not approval:
        return _err("approval not found", 404)
    approvals_repo.delete(approval_id)
    logger.info("Approval %s deleted by admin", approval_id)
    return _ok({"deleted": True})


def delete_approval_handler(event, context):
    approval_id = (event.get("pathParameters") or {}).get("approval_id", "")
    logger.info("DELETE /admin/approvals/%s", approval_id)
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_delete_approval(approval_id, token)


def list_approvals_handler(event, context):
    logger.info("GET /admin/approvals")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    qs = event.get("queryStringParameters") or {}
    return handle_list_approvals(qs, token)


def review_approval_handler(event, context):
    approval_id = (event.get("pathParameters") or {}).get("approval_id", "")
    action = (event.get("pathParameters") or {}).get("action", "")
    logger.info("PUT /admin/approvals/%s/%s", approval_id, action)
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    raw_body = event.get("body") or "{}"
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        body = {}
    return handle_review_approval(approval_id, action, token, body)
