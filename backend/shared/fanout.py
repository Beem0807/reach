"""Shared fan-out blast-radius controls.

A fan-out (fleet or tag) runs one command across many hosts at once. There is no
enforced human gate over MCP - an AI agent auto-proceeds - so the cap here is the
server-side seatbelt: above `DEFAULT_FANOUT_CAP` targets a caller must *explicitly*
opt into the blast radius via `max_targets`, so a hallucinated or fat-fingered call
can't hit an entire large fleet in one unconfirmed shot.
"""
import hashlib

from shared.settings import SETTINGS_DEFAULTS

# Ultimate fallback cap when neither the fleet (max_fanout) nor the tenant setting
# (fanout_cap) is set. The effective cap is resolved per fan-out from those.
DEFAULT_FANOUT_CAP = SETTINGS_DEFAULTS["fanout_cap"]


def deterministic_run_id(tenant_id, key):
    """A run_id derived from the tenant + idempotency key, so a retried fan-out with
    the same key maps to the *same* run - and a replay is detected by a plain
    list_by_run (no separate index). Only used when a key is supplied."""
    h = hashlib.sha256(f"{tenant_id}:{key}".encode()).hexdigest()[:22]
    return "run_" + h


# Bound the stored who-and-why detail so a run over a huge fleet keeps a small row.
RUN_DETAIL_CAP = 50


def new_run_row(run_id, tenant_id, created_by, command, created_at, dispatched,
                skipped, fleet_id=None, tag=None, idempotency_key=None,
                rollout=None, wave_total=1):
    """The first-class run record written at fan-out time. Captures intent - the counts
    AND a bounded who/why list of skipped members - that member jobs can't hold, and seeds
    cached counts/state (jobs start PENDING) refreshed as results land.

    `skipped` is [{agent_id, hostname, reason}] (members that couldn't run - inactive,
    read-only access, unapproved). There is no "capping": every eligible member runs, in
    waves of the fan-out cap. For a staged (waved) run, `rollout` is the resolved plan and
    `wave_total` > 1; the run starts on wave 0 with the later waves' jobs HELD (counted
    under `pending`)."""
    return {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "fleet_id": fleet_id,
        "tag": tag,
        "command": command,
        "created_by": created_by,
        "created_at": created_at,
        "dispatched": dispatched,
        "skipped_count": len(skipped),
        "skipped": skipped[:RUN_DETAIL_CAP],
        "idempotency_key": idempotency_key,
        "state": "running" if dispatched else "empty",
        "counts": {"ok": 0, "failed": 0, "pending": dispatched, "running": 0},
        "parent_run_id": None,
        "rollout": rollout,
        "current_wave": 0,
        "wave_total": wave_total,
    }


def run_summary_view(run):
    """A run row -> the compact list entry the console/CLI runs views render (members
    + pass/fail/pending counts), from the run's cached counts (no job scan)."""
    counts = run.get("counts") or {}
    return {
        "run_id": run.get("run_id"),
        "command": run.get("command"),
        "created_at": run.get("created_at"),
        "created_by": run.get("created_by"),
        "tag": run.get("tag"),
        "state": run.get("state"),
        "members": run.get("dispatched") or 0,
        "ok": counts.get("ok", 0),
        "failed": counts.get("failed", 0),
        "pending": (counts.get("pending", 0) + counts.get("running", 0)),
    }


def aggregate_run(jobs):
    """Summarize a run's member jobs into {state, counts, total, terminal}.

    counts: ok (succeeded, exit 0/None) / failed (FAILED/nonzero/EXPIRED/REJECTED) /
    pending / running. A **HELD** (staged, not-yet-released) job counts as `pending` - it
    will run, just not yet - so a staged run is never falsely terminal. A **CANCELED**
    job (a staged wave the operator cancelled) never ran, so it's neither ok nor failed
    (ignored). `terminal` when nothing is still pending/running/held.
    state: pending -> running -> (succeeded | partial | failed)."""
    counts = {"ok": 0, "failed": 0, "pending": 0, "running": 0}
    for j in jobs:
        st = j.get("status")
        if st in ("PENDING", "HELD"):
            counts["pending"] += 1
        elif st == "RUNNING":
            counts["running"] += 1
        elif st == "SUCCEEDED" and (j.get("exit_code") in (0, None)):
            counts["ok"] += 1
        elif st == "CANCELED":
            continue  # a cancelled staged wave - never ran, not a failure
        else:
            counts["failed"] += 1
    terminal = (counts["pending"] + counts["running"]) == 0
    if not terminal:
        state = "running" if (counts["running"] or counts["ok"] or counts["failed"]) else "pending"
    elif counts["failed"] == 0:
        state = "succeeded"
    elif counts["ok"] == 0:
        state = "failed"
    else:
        state = "partial"
    return {"state": state, "counts": counts, "total": len(jobs), "terminal": terminal}


def parse_max_targets(raw):
    """Coerce a request's `max_targets` to a positive int, or None if absent.
    Returns (value_or_None, error_or_None)."""
    if raw is None:
        return None, None
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return None, "max_targets must be an integer"
    if n < 1:
        return None, "max_targets must be >= 1"
    return n, None


def order_and_limit(eligible, max_targets=None, cap=DEFAULT_FANOUT_CAP):
    """Order the eligible targets deterministically (hostname, agent_id) and resolve the
    per-wave **size** = the fan-out cap, which `max_targets` may lower but never raise.

    Returns (ordered, wave_size, error). An explicit `max_targets` above the cap is refused
    (error set). Every eligible member runs, in waves of `wave_size` - there is no capping/
    dropping. Ordering is stable so the first wave hits the same hosts each call."""
    ordered = sorted(eligible, key=lambda a: ((a.get("hostname") or ""), a.get("agent_id") or ""))
    if max_targets is not None and max_targets > cap:
        return ordered, None, (
            f"max_targets={max_targets} exceeds this fleet's fan-out cap of {cap}. The cap "
            f"is set on the fleet and can't be overridden - lower max_targets, or raise the "
            f"fleet's cap.")
    limit = min(max_targets, cap) if max_targets is not None else cap
    return ordered, limit, None
