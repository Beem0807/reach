"""Per-tenant settings: retention windows + the fan-out blast-radius cap.

Two audiences edit the same ``tenant.settings`` blob:

  * the **tenant admin** (``/tenant/settings``) - may set values within
    SETTINGS_BOUNDS only; this is the self-service console page.
  * the **platform admin** (``/admin/tenants/{id}/settings``) - may set any
    positive value, overriding the tenant's bounds, for support/override.

A value cleared (sent as ``null``) falls back to the platform default. The
effective value is always the tenant's stored override merged over the defaults
(see shared.settings.effective_settings).
"""
import json
import logging

import shared.audit as audit
from shared.admin_auth import verify_session_token as _verify_admin
from shared.auth import _verify_tenant_payload
from shared.response import _err, _ok
from shared.settings import (SETTINGS_BOUNDS, SETTINGS_DEFAULTS, SETTINGS_KEYS,
                             effective_settings, effective_wave_policy, merge_settings,
                             validate_settings, wave_policy_exceeds_cap)
from shared.store import tenants_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _settings_view(tenant: dict) -> dict:
    """The shape the console/CLI render: the effective (in-force) value per key, the
    tenant's raw overrides (so the UI can show 'default' vs 'overridden'), plus the
    platform defaults and tenant-admin bounds for the form."""
    stored = (tenant or {}).get("settings") or {}
    effective = effective_settings(tenant)
    return {
        "settings": effective,
        "overrides": {k: stored[k] for k in SETTINGS_KEYS if isinstance(stored.get(k), int)
                      and not isinstance(stored.get(k), bool)},
        "defaults": dict(SETTINGS_DEFAULTS),
        "bounds": {k: list(v) for k, v in SETTINGS_BOUNDS.items()},
        # Staged-rollout policy: {tag/fleet x read/write -> {mode, on_failure}}.
        "wave_policy": effective_wave_policy(tenant),
    }


def _wave_cap_error(settings: dict):
    """Reject a wave policy whose concurrency exceeds the (merged) fan-out cap - a wave
    can never run more hosts than the cap. Returns an error string or None."""
    cap = effective_settings({"settings": settings})["fanout_cap"]
    over = wave_policy_exceeds_cap(settings.get("wave_policy"), cap)
    if over is not None:
        return f"wave concurrency {over} cannot exceed the fan-out cap ({cap})"
    return None


# --- tenant admin (self-service, bounded) -----------------------------------
# Settings are part of the sensitive console tier, so like user management they require
# a real session login (JWT), not an API token - _verify_tenant_payload has no tok_
# fallback. Only the tenant admin may read or change them.

def handle_get_tenant_settings(raw_token: str) -> dict:
    tp = _verify_tenant_payload(raw_token)
    if not tp:
        return _err("unauthorized", 401)
    if tp.get("role") != "admin":
        return _err("only a tenant admin can view settings", 403)
    tenant = tenants_repo.get(tp["tenant_id"])
    if not tenant:
        return _err("tenant not found", 404)
    return _ok(_settings_view(tenant))


def handle_update_tenant_settings(body: dict, raw_token: str, ip: str = "") -> dict:
    tp = _verify_tenant_payload(raw_token)
    if not tp:
        return _err("unauthorized", 401)
    if tp.get("role") != "admin":
        return _err("only a tenant admin can change settings", 403)
    tenant = tenants_repo.get(tp["tenant_id"])
    if not tenant:
        return _err("tenant not found", 404)

    patch, err = validate_settings(body or {}, enforce_bounds=True)
    if err:
        return _err(err, 400)
    merged = merge_settings(tenant.get("settings") or {}, patch)
    cap_err = _wave_cap_error(merged)
    if cap_err:
        return _err(cap_err, 400)
    tenants_repo.set_settings(tp["tenant_id"], merged)
    audit.write("tenant.settings_updated", tenant_id=tp["tenant_id"],
                actor_id=tp["sub"], actor_name=tp.get("username"), actor_role=tp.get("role"),
                resource_type="tenant", resource_id=tp["tenant_id"],
                metadata={"keys": sorted(patch.keys())}, ip_address=ip)
    return _ok(_settings_view({**tenant, "settings": merged}))


# --- platform admin (override, unbounded) -----------------------------------

def handle_admin_get_tenant_settings(tenant_id: str, raw_token: str) -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    tenant = tenants_repo.get(tenant_id)
    if not tenant:
        return _err("tenant not found", 404)
    return _ok(_settings_view(tenant))


def handle_admin_update_tenant_settings(tenant_id: str, body: dict, raw_token: str, ip: str = "") -> dict:
    if not _verify_admin(raw_token):
        return _err("unauthorized", 401)
    tenant = tenants_repo.get(tenant_id)
    if not tenant:
        return _err("tenant not found", 404)
    # Platform admin overrides tenant bounds.
    patch, err = validate_settings(body or {}, enforce_bounds=False)
    if err:
        return _err(err, 400)
    merged = merge_settings(tenant.get("settings") or {}, patch)
    cap_err = _wave_cap_error(merged)
    if cap_err:
        return _err(cap_err, 400)
    tenants_repo.set_settings(tenant_id, merged)
    audit.write("tenant.settings_overridden", tenant_id=tenant_id,
                resource_type="tenant", resource_id=tenant_id,
                metadata={"keys": sorted(patch.keys()), "by": "platform_admin"}, ip_address=ip)
    return _ok(_settings_view({**tenant, "settings": merged}))


# --- Lambda entrypoints ------------------------------------------------------

def _token_from_event(event: dict) -> str:
    headers = event.get("headers") or {}
    token = headers.get("authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _ip_from_event(event: dict) -> str:
    return ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "")


def get_tenant_settings_handler(event, context):
    logger.info("GET /tenant/settings")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_get_tenant_settings(token)


def update_tenant_settings_handler(event, context):
    logger.info("PUT /tenant/settings")
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_update_tenant_settings(body, token, _ip_from_event(event))


def admin_get_tenant_settings_handler(event, context):
    path = event.get("pathParameters") or {}
    tenant_id = path.get("tenant_id", "")
    logger.info("GET /admin/tenants/%s/settings", tenant_id)
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_admin_get_tenant_settings(tenant_id, token)


def admin_update_tenant_settings_handler(event, context):
    path = event.get("pathParameters") or {}
    tenant_id = path.get("tenant_id", "")
    logger.info("PUT /admin/tenants/%s/settings", tenant_id)
    token = _token_from_event(event)
    if not token:
        return _err("missing Authorization header", 401)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err("invalid JSON body")
    return handle_admin_update_tenant_settings(tenant_id, body, token, _ip_from_event(event))
