import json
import logging
import os

from shared.response import _err, _ok
from shared.store import agents_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]

VALID_MODES = ("wild", "readonly", "approved")


def _verify_admin(raw: str) -> bool:
    import hmac
    return hmac.compare_digest(raw, ADMIN_TOKEN)


def handle_get_policy(agent_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)
    return _ok({
        "agent_id": agent_id,
        "mode": agent.get("mode", "wild"),
        "approved_commands": agent.get("approved_commands") or [],
    })


def handle_set_mode(agent_id: str, body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    mode = body.get("mode", "").strip()
    if mode not in VALID_MODES:
        return _err(f"mode must be one of: {', '.join(VALID_MODES)}")
    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)
    approved = agent.get("approved_commands") or []
    agents_repo.update_policy(agent_id, mode, approved)
    logger.info("Set agent %s mode → %s", agent_id, mode)
    return _ok({"agent_id": agent_id, "mode": mode, "approved_commands": approved})


def handle_add_command(agent_id: str, body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    commands = body.get("commands", [])
    if isinstance(commands, str):
        commands = [commands]
    commands = [c.strip() for c in commands if c.strip()]
    if not commands:
        return _err("commands required")
    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)
    existing = list(agent.get("approved_commands") or [])
    added = [c for c in commands if c not in existing]
    already_exists = [c for c in commands if c in existing]
    approved = existing + added
    if added:
        agents_repo.update_policy(agent_id, agent.get("mode", "wild"), approved)
    logger.info("Added %d command(s) for agent %s", len(added), agent_id)
    return _ok({
        "agent_id": agent_id,
        "approved_commands": approved,
        "added": added,
        "already_exists": already_exists,
    })


def handle_remove_command(agent_id: str, body: dict, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    commands = body.get("commands", [])
    if isinstance(commands, str):
        commands = [commands]
    commands = [c.strip() for c in commands if c.strip()]
    if not commands:
        return _err("commands required")
    agent = agents_repo.get(agent_id)
    if not agent:
        return _err("agent not found", 404)
    existing = list(agent.get("approved_commands") or [])
    removed = [c for c in commands if c in existing]
    not_found = [c for c in commands if c not in existing]
    approved = [c for c in existing if c not in commands]
    if removed:
        agents_repo.update_policy(agent_id, agent.get("mode", "wild"), approved)
    logger.info("Removed %d command(s) for agent %s", len(removed), agent_id)
    return _ok({
        "agent_id": agent_id,
        "approved_commands": approved,
        "removed": removed,
        "not_found": not_found,
    })


# ---------------------------------------------------------------------------
# Lambda handlers
# ---------------------------------------------------------------------------

def _token_from_event(event: dict) -> str:
    auth = (event.get("headers") or {}).get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def get_policy_handler(event, context):
    logger.info("GET /admin/agents/{agent_id}/policy")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    return handle_get_policy(agent_id, token)


def set_mode_handler(event, context):
    logger.info("PUT /admin/agents/{agent_id}/policy/mode")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_set_mode(agent_id, body, token)


def add_command_handler(event, context):
    logger.info("POST /admin/agents/{agent_id}/policy/commands")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_add_command(agent_id, body, token)


def remove_command_handler(event, context):
    logger.info("DELETE /admin/agents/{agent_id}/policy/commands")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    agent_id = (event.get("pathParameters") or {}).get("agent_id", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_remove_command(agent_id, body, token)
