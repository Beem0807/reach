import json
import logging
import secrets

from shared.auth import AGENT_TOKEN_PREFIX, _bearer, _hmac_token, _verify_agent_token
from shared.response import _err, _iso, _ok
from shared.store import agents_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_agent_rotate_token(body: dict, raw_token: str) -> dict:
    machine_fp = body.get("machine_fingerprint", "").strip()

    if not machine_fp:
        return _err("machine_fingerprint required", 400)

    # Credential-only: the (old) agent token identifies the agent; no agent_id.
    agent = _verify_agent_token(raw_token)
    if not agent:
        return _err("unauthorized", 401)
    agent_id = agent["agent_id"]

    if agent.get("status") not in ("ACTIVE", "INACTIVE"):
        return _err("agent not active", 403)

    if agent.get("machine_fingerprint") != machine_fp:
        return _err("fingerprint mismatch", 403)

    new_raw_token = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
    agents_repo.update_agent_token_hash(agent_id, _hmac_token(new_raw_token), _iso())

    logger.info("Rotated agent token for agent=%s", agent_id)

    return _ok({"agent_token": new_raw_token})


def agent_rotate_token_handler(event, context):
    logger.info("POST /agent/rotate-token")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_agent_rotate_token(body, token)
