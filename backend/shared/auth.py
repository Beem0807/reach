import hashlib
import hmac
import os
from typing import Optional

TOKEN_PEPPER = os.environ["TOKEN_PEPPER"]

AGENT_TOKEN_PREFIX = "agent_"
TENANT_TOKEN_PREFIX = "tok_"
INSTALL_TOKEN_PREFIX = "install_"


def _hmac_token(raw: str) -> str:
    return hmac.new(TOKEN_PEPPER.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _verify_tenant_token(raw: str) -> Optional[dict]:
    from .store import tokens_repo
    return tokens_repo.get_by_hash(_hmac_token(raw))


def _verify_agent_token(raw: str, agent_id: str) -> Optional[dict]:
    from .store import agents_repo
    agent = agents_repo.get(agent_id)
    if not agent:
        return None
    if not hmac.compare_digest(_hmac_token(raw), agent.get("agent_token_hash", "")):
        return None
    return agent


def _bearer(event: dict) -> Optional[str]:
    auth = (event.get("headers") or {}).get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None
