"""Staged rollout ("waves") for a fan-out.

A normal fan-out dispatches to every target at once. A **staged** fan-out splits the
ordered targets into waves and runs them wave by wave. Wave 0 dispatches immediately
(jobs PENDING); later waves are created HELD and released one at a time.

Two knobs, configurable per tenant (and overridable per fleet), per read/write command:

  * **wave strategy** - a batch size ("how many at a time") plus a mode:
        mode "auto"   -> the next wave releases automatically once a wave finishes clean.
        mode "manual" -> pause after every wave; an operator/AI resumes to continue.
  * **failure policy** - what a wave containing any failure does:
        "stop"     -> pause the rollout, holding the remaining waves (the default).
        "continue" -> keep rolling despite failures.

So a manual/stop rollout stops for confirmation after each wave and halts on any failure;
an auto/continue rollout rolls straight through. A manually paused/cancelled run is never
auto-advanced (see advance_waves).

Rollout shapes (all optional; no size => single wave, i.e. not staged):
  {"batch": 5}                 -> [5, 5, 5, ...]  "5 at a time" until all are covered
  {"canary": 5}                -> [5, <rest>]     (explicit per-call sizing)
  {"waves": [5, 20]}           -> [5, 20, <rest>] (explicit; given sizes are prefixes)
  {..., "mode": "auto|manual", "on_failure": "stop|continue"}

The tenant/fleet **wave policy** uses the ``batch`` form; canary/explicit waves remain
available for a one-off per-call rollout.
"""
from typing import Optional

# A run is never split into more waves than this (guards a pathological plan, e.g.
# batch=1 over a huge fleet). Raise the batch size if you hit it.
MAX_WAVES = 50

MODES = ("auto", "manual")
ON_FAILURE = ("stop", "continue")
DEFAULT_MODE = "auto"
DEFAULT_ON_FAILURE = "stop"

# Platform default wave policy, applied per read/write when neither the fleet nor the
# tenant configured one. Reads are safe -> roll fast and keep going; writes are risky ->
# pause after every wave (manual) and stop on the first failure.
DEFAULT_POLICY = {
    "read":  {"mode": "auto",   "on_failure": "continue"},
    "write": {"mode": "manual", "on_failure": "stop"},
}


def plan_waves(total: int, rollout: Optional[dict]) -> "tuple[list[int] | None, str | None]":
    """Resolve a rollout into concrete wave sizes over ``total`` ordered targets. With no
    size (no batch/canary/waves) the result is a single wave ``[total]`` - not staged.

    ``batch`` repeats a fixed size ("N at a time"); ``canary``/``waves`` are prefix sizes
    with the remainder forming a final wave. Returns ``(wave_sizes, error)``."""
    if total < 1:
        return [], None
    if not rollout:
        return [total], None

    if "batch" in rollout and rollout["batch"] not in (None, "", 0):
        try:
            b = int(rollout["batch"])
        except (TypeError, ValueError):
            return None, "batch must be an integer"
        if b < 1:
            return None, "batch must be >= 1"
        sizes, remaining = [], total
        while remaining > 0:
            take = min(b, remaining)
            sizes.append(take)
            remaining -= take
        if len(sizes) > MAX_WAVES:
            return None, f"too many waves (max {MAX_WAVES}); increase the batch size"
        return sizes, None

    if "canary" in rollout and rollout["canary"] not in (None, "", 0):
        try:
            k = int(rollout["canary"])
        except (TypeError, ValueError):
            return None, "canary must be an integer"
        if k < 1:
            return None, "canary must be >= 1"
        prefixes = [k]
    elif rollout.get("waves"):
        raw = rollout["waves"]
        if not isinstance(raw, list):
            return None, "waves must be a non-empty list of positive integers"
        prefixes = []
        for x in raw:
            try:
                xi = int(x)
            except (TypeError, ValueError):
                return None, "waves must be integers"
            if xi < 1:
                return None, "each wave size must be >= 1"
            prefixes.append(xi)
    else:
        return [total], None  # rollout with mode/on_failure but no size -> not staged

    sizes: list = []
    used = 0
    for s in prefixes:
        if used >= total:
            break
        take = min(s, total - used)
        sizes.append(take)
        used += take
    if used < total:
        sizes.append(total - used)
    if len(sizes) > MAX_WAVES:
        return None, f"too many waves (max {MAX_WAVES})"
    return sizes, None


def rollout_meta(rollout: Optional[dict]) -> dict:
    """The advancement knobs from a rollout, with defaults: {"mode", "on_failure"}."""
    r = rollout or {}
    mode = r.get("mode") if r.get("mode") in MODES else DEFAULT_MODE
    on_failure = r.get("on_failure") if r.get("on_failure") in ON_FAILURE else DEFAULT_ON_FAILURE
    return {"mode": mode, "on_failure": on_failure}


def validate_wave_strategy(entry) -> "tuple[dict | None, str | None]":
    """Validate one wave-policy entry: ``{"mode": auto|manual, "on_failure": stop|continue,
    "concurrency"?: int}``. ``concurrency`` is how many hosts run per wave; it defaults to
    the fan-out cap and may only lower it (the caller enforces ``<= cap`` - see
    wave_policy_exceeds_cap). ``None`` / empty (no mode/on_failure) => no policy (not
    staged). Returns ``(clean_entry | None, error | None)``."""
    if entry is None:
        return None, None
    if not isinstance(entry, dict):
        return None, "wave strategy must be an object"
    mode = entry.get("mode")
    on_failure = entry.get("on_failure")
    if not mode and not on_failure:
        return None, None  # empty -> no policy
    mode = mode or DEFAULT_MODE
    on_failure = on_failure or DEFAULT_ON_FAILURE
    if mode not in MODES:
        return None, f"mode must be one of {MODES}"
    if on_failure not in ON_FAILURE:
        return None, f"on_failure must be one of {ON_FAILURE}"
    clean = {"mode": mode, "on_failure": on_failure}
    conc = entry.get("concurrency")
    if conc not in (None, "", 0):
        try:
            ci = int(conc)
        except (TypeError, ValueError):
            return None, "concurrency must be an integer"
        if ci < 1:
            return None, "concurrency must be >= 1"
        clean["concurrency"] = ci
    return clean, None


def iter_policy_concurrencies(policy):
    """Yield every ``concurrency`` value in a wave policy (tenant-shaped {scope:{rw:entry}}
    or fleet-shaped {rw:entry}). Used to enforce concurrency <= cap."""
    if not isinstance(policy, dict):
        return
    if "mode" in policy:  # a leaf strategy entry
        c = policy.get("concurrency")
        if isinstance(c, int) and not isinstance(c, bool):
            yield c
        return
    for v in policy.values():
        yield from iter_policy_concurrencies(v)


def resolve_policy(is_write: bool, tenant: dict, scope: str, fleet: dict = None) -> dict:
    """The staged-rollout policy (mode + on_failure) for a fan-out. Precedence: a fleet-level
    override (fleet runs) beats the tenant's fleet default, which beats the platform
    DEFAULT_POLICY; a tag run uses the tenant tag policy, else the default. Reads and writes
    resolve independently. Always returns a policy (never None). ``scope`` is "fleet"/"tag"."""
    rw = "write" if is_write else "read"
    policy = ((tenant or {}).get("settings") or {}).get("wave_policy") or {}
    if scope == "fleet":
        override = (fleet or {}).get("wave_policy") or {}
        if rw in override:
            return override[rw]
        return (policy.get("fleet") or {}).get(rw) or DEFAULT_POLICY[rw]
    return (policy.get("tag") or {}).get(rw) or DEFAULT_POLICY[rw]


def assign_waves(targets: list, wave_sizes: list) -> "list[tuple[dict, int]]":
    """Pair each ordered target with its wave index per ``wave_sizes``. ``targets`` is
    already ordered deterministically by the fan-out, so wave membership is stable."""
    pairs: list = []
    idx = 0
    for wave, size in enumerate(wave_sizes):
        for _ in range(size):
            if idx >= len(targets):
                break
            pairs.append((targets[idx], wave))
            idx += 1
    last = len(wave_sizes) - 1
    while idx < len(targets):
        pairs.append((targets[idx], last))
        idx += 1
    return pairs


def _wave_of(job: dict) -> int:
    return job.get("wave") or 0


def _is_terminal(job: dict) -> bool:
    return job.get("status") not in ("PENDING", "RUNNING", "HELD")


def _is_failed(job: dict) -> bool:
    st = job.get("status")
    if st in ("PENDING", "RUNNING", "HELD", "CANCELED"):
        return False
    return not (st == "SUCCEEDED" and job.get("exit_code") in (0, None))


def advance_waves(run: dict, jobs: list, agg: dict) -> dict:
    """Decide a staged run's next state from its member jobs. **Pure** - it releases
    nothing; the caller acts on the returned ``release_wave`` (flip that wave HELD->PENDING).

    Honors the rollout's ``mode`` and ``on_failure``: after a wave finishes, the run stops
    (pauses) if the wave had a failure and on_failure is "stop", or if mode is "manual"
    (confirmation after every wave); otherwise the next wave releases. A manually
    paused/cancelled run is left as-is (resume is explicit).

    Returns ``{state, current_wave, release_wave}`` (release_wave is the wave to release
    now, or None)."""
    stored = run.get("state")
    cw = run.get("current_wave") or 0
    wt = run.get("wave_total") or 1
    rollout = run.get("rollout") or {}
    meta = rollout_meta(rollout)

    if stored in ("canceled", "paused"):
        return {"state": stored, "current_wave": cw, "release_wave": None}

    current = [j for j in jobs if _wave_of(j) == cw]
    if current and not all(_is_terminal(j) for j in current):
        return {"state": "running", "current_wave": cw, "release_wave": None}

    if cw < wt - 1:
        wave_failed = any(_is_failed(j) for j in current)
        if wave_failed and meta["on_failure"] == "stop":
            return {"state": "paused", "current_wave": cw, "release_wave": None}
        if meta["mode"] == "manual":
            return {"state": "paused", "current_wave": cw, "release_wave": None}
        return {"state": "running", "current_wave": cw + 1, "release_wave": cw + 1}

    # Last wave complete - the run reaches its natural terminal state.
    return {"state": agg["state"], "current_wave": cw, "release_wave": None}
