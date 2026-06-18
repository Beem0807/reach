import json
import logging
import os

from shared.response import _err, _ok
from shared.store import agents_repo, approvals_repo

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
    approved_commands = [a["command"] for a in approvals_repo.list_by_agent(agent_id, status="approved")]
    return _ok({
        "agent_id": agent_id,
        "mode": agent.get("mode", "wild"),
        "approved_commands": approved_commands,
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
    agents_repo.update_policy(agent_id, mode)
    logger.info("Set agent %s mode → %s", agent_id, mode)
    return _ok({"agent_id": agent_id, "mode": mode})


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
