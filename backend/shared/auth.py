import hashlib
import hmac
import os
from typing import Optional

TOKEN_PEPPER = os.environ["TOKEN_PEPPER"]

AGENT_TOKEN_PREFIX = "agent_"
INSTALL_TOKEN_PREFIX = "install_"
# Fleet join token: reusable, fleet-wide. Distinct prefix so the claim path can
# route it to the fleet lookup instead of the per-agent install-token lookup.
FLEET_TOKEN_PREFIX = "fleet_"


def _hmac_token(raw: str) -> str:
    return hmac.new(TOKEN_PEPPER.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _verify_api_key(raw: str) -> Optional[dict]:
    """Verify a tok_... API key against the database. Returns the owning user or None."""
    from .store import api_tokens_repo, tenants_repo, users_repo
    token_hash = _hmac_token(raw)
    token = api_tokens_repo.get_by_hash(token_hash)
    if not token or token.get("status") != "ACTIVE":
        return None
    user = users_repo.get(token["user_id"])
    if not user or user.get("status") == "REVOKED":
        return None
    tenant = tenants_repo.get(user.get("tenant_id", ""))
    if tenant and tenant.get("status") == "DISABLED":
        return None
    from .response import _iso
    api_tokens_repo.touch(token["token_id"], _iso())
    return user


def _verify_tenant_token(raw: str) -> Optional[dict]:
    from .store import tenants_repo, users_repo
    from .tenant_auth import verify_tenant_token as _jwt_verify
    payload = _jwt_verify(raw)
    if payload:
        user = users_repo.get(payload["sub"])
        # A disabled (REVOKED) user must lose access immediately - existing sessions
        # and API tokens are cut, not just future logins.
        if not user or user.get("status") == "REVOKED":
            return None
        tenant = tenants_repo.get(user.get("tenant_id", ""))
        if tenant and tenant.get("status") == "DISABLED":
            return None
        return user
    # Fall through: try API key (tok_... format stored in DB)
    if raw.startswith("tok_"):
        return _verify_api_key(raw)
    return None


def _verify_tenant_payload(raw: str) -> Optional[dict]:
    """Verify JWT and confirm tenant is still active. Returns JWT payload or None."""
    from .store import tenants_repo
    from .tenant_auth import verify_tenant_token as _jwt_verify
    payload = _jwt_verify(raw)
    if not payload:
        return None
    tenant = tenants_repo.get(payload.get("tenant_id", ""))
    if not tenant or tenant.get("status") == "DISABLED":
        return None
    return payload


def _verify_agent_token(raw: str) -> Optional[dict]:
    # Credential-only: the agent token identifies the agent. We hash the bearer
    # token and look the agent up by that hash, so no client-supplied agent_id is
    # needed (or trusted). Hashing first means the lookup carries no timing oracle
    # on the secret.
    from .store import agents_repo
    if not raw:
        return None
    return agents_repo.get_by_agent_token_hash(_hmac_token(raw))


def _bearer(event: dict) -> Optional[str]:
    auth = (event.get("headers") or {}).get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None
