"""Per-tenant settings: retention windows + the fan-out blast-radius cap.

Stored on the tenant record (``tenant.settings``). Formerly deployment-wide env vars;
now each tenant has its own, editable by the tenant admin and overridable by the
platform admin. The *effective* value is the tenant's setting when present, else the
platform default below.

Audit logs live at two scopes: **tenant-level** entries (a tenant's own trail) are
governed by the per-tenant ``audit_retention_days`` here; **platform-level** entries
(``tenant_id IS NULL`` - platform-admin / cross-tenant actions) are governed by the
platform-wide AUDIT_RETENTION_DAYS env var and are not tenant-settable.
"""

# Platform defaults - the effective value when a tenant hasn't set its own.
SETTINGS_DEFAULTS = {
    "approval_retention_days": 7,
    "job_retention_days": 7,
    "run_retention_days": 30,
    "audit_retention_days": 90,
    "agent_history_retention_days": 30,
    "fanout_cap": 25,
}

# Bounds a **tenant admin** may set within. The platform admin can set any positive
# value (it overrides these) via the admin endpoint.
SETTINGS_BOUNDS = {
    "approval_retention_days": (1, 3650),
    "job_retention_days": (1, 3650),
    "run_retention_days": (1, 3650),
    "audit_retention_days": (1, 3650),
    "agent_history_retention_days": (1, 3650),
    "fanout_cap": (1, 100000),
}

SETTINGS_KEYS = tuple(SETTINGS_DEFAULTS)

# The staged-rollout policy is a structured (non-int) setting, stored under this key in
# tenant.settings. Shape: {"tag": {"read": <strategy|null>, "write": ...},
#                           "fleet": {"read": ..., "write": ...}}  (fleet = tenant default).
WAVE_POLICY_KEY = "wave_policy"
_WAVE_SCOPES = ("tag", "fleet")
_WAVE_RW = ("read", "write")


def effective_settings(tenant) -> dict:
    """The tenant's effective settings: its stored overrides merged over the defaults.
    Always returns every (numeric) key with an int value. The structured wave_policy is
    separate - see effective_wave_policy."""
    stored = (tenant or {}).get("settings") or {}
    out = {}
    for key, default in SETTINGS_DEFAULTS.items():
        v = stored.get(key)
        out[key] = v if isinstance(v, int) and not isinstance(v, bool) else default
    return out


def effective_wave_policy(tenant) -> dict:
    """The tenant's stored staged-rollout policy ({tag/fleet x read/write}), or {}."""
    pol = ((tenant or {}).get("settings") or {}).get(WAVE_POLICY_KEY)
    return pol if isinstance(pol, dict) else {}


def validate_wave_policy(policy) -> "tuple[dict | None, str | None]":
    """Validate the tenant wave_policy object. ``None`` clears it. Each scope (tag/fleet)
    maps read/write to a wave strategy (validated by shared.waves.validate_wave_strategy).
    Missing branches are fine (fall back to no staging). Returns (clean | None, error)."""
    from shared.waves import validate_wave_strategy
    if policy is None:
        return None, None
    if not isinstance(policy, dict):
        return None, "wave_policy must be an object"
    clean: dict = {}
    for scope in _WAVE_SCOPES:
        sc = policy.get(scope)
        if sc is None:
            continue
        if not isinstance(sc, dict):
            return None, f"wave_policy.{scope} must be an object"
        entry: dict = {}
        for rw in _WAVE_RW:
            strat, err = validate_wave_strategy(sc.get(rw))
            if err:
                return None, f"wave_policy.{scope}.{rw}: {err}"
            if strat is not None:
                entry[rw] = strat
        if entry:
            clean[scope] = entry
    return (clean or None), None


def validate_fleet_wave_policy(policy) -> "tuple[dict | None, str | None]":
    """Validate a fleet-level wave-policy override: ``{"read": <strategy|null>, "write": ...}``
    (no tag/fleet scope - a fleet only governs its own fleet runs). ``None`` clears it.
    Returns (clean | None, error)."""
    from shared.waves import validate_wave_strategy
    if policy is None:
        return None, None
    if not isinstance(policy, dict):
        return None, "wave_policy must be an object"
    clean: dict = {}
    for rw in _WAVE_RW:
        strat, err = validate_wave_strategy(policy.get(rw))
        if err:
            return None, f"wave_policy.{rw}: {err}"
        if strat is not None:
            clean[rw] = strat
    return (clean or None), None


def wave_policy_exceeds_cap(policy, cap: int):
    """The first wave-policy concurrency that exceeds ``cap``, or None. Concurrency (hosts
    per wave) can lower the fan-out cap but never raise it."""
    from shared.waves import iter_policy_concurrencies
    for c in iter_policy_concurrencies(policy):
        if c > cap:
            return c
    return None


def validate_settings(body: dict, enforce_bounds: bool = True) -> "tuple[dict | None, str | None]":
    """Validate a settings patch. Numeric keys must be positive ints (within SETTINGS_BOUNDS
    when ``enforce_bounds``; the platform admin passes False to override beyond them). The
    structured ``wave_policy`` key is validated separately.

    Returns (clean_settings, error). ``None`` for a key clears it back to the default."""
    clean: dict = {}
    for key, value in (body or {}).items():
        if key == WAVE_POLICY_KEY:
            wp, err = validate_wave_policy(value)
            if err:
                return None, err
            clean[WAVE_POLICY_KEY] = wp  # None clears
            continue
        if key not in SETTINGS_DEFAULTS:
            continue  # ignore unknown keys
        if value is None:
            clean[key] = None  # clear -> falls back to the platform default
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return None, f"{key} must be a positive integer"
        if value < 1:
            return None, f"{key} must be a positive integer"
        if enforce_bounds:
            lo, hi = SETTINGS_BOUNDS[key]
            if not (lo <= value <= hi):
                return None, f"{key} must be between {lo} and {hi}"
        clean[key] = value
    return clean, None


def merge_settings(existing: dict, patch: dict) -> dict:
    """Apply a validated patch to the stored settings: set ints, drop keys set to None."""
    result = dict(existing or {})
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result
