"""Run (fan-out) status - a first-class handle over a fan-out so callers (a human or an
AI over MCP) can poll "is run X done, what failed?" across turns. Reads the durable run
row for identity/intent and refreshes its cached counts from the member jobs, so the
summary stays authoritative even after the jobs are purged on retention."""
import logging

from shared.access import can_access_agent, can_access_fleet
from shared.auth import _bearer, _verify_tenant_token
from shared.fanout import aggregate_run
from shared.response import _err, _now, _ok
from shared.store import agents_repo, fleets_repo, jobs_repo, runs_repo
from shared.waves import advance_waves

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_TERMINAL_STATES = {"succeeded", "partial", "failed", "empty", "canceled"}
# Cap the failure drill-down so a run over a huge fleet can't blow up the response
# (or an MCP client's context). stderr is truncated per member.
_MAX_FAILURES = 50
_STDERR_SNIPPET = 500


def _failed(job: dict) -> bool:
    st = job.get("status")
    if st in ("PENDING", "RUNNING"):
        return False
    return not (st == "SUCCEEDED" and job.get("exit_code") in (0, None))


def _release_wave(run_id: str, wave: int) -> list:
    """Flip a staged run's next wave HELD->PENDING and reactivate those agents so they
    pick the jobs up on their next sync."""
    released = jobs_repo.release_wave(run_id, wave)
    now = _now()
    for j in released:
        agents_repo.set_active_until(j["agent_id"], now + 120)
    return released


def refresh_run(tenant_id: str, run_id):
    """Recompute a run's cached counts/state from its member jobs and persist them.
    Called when a result lands (and lazily on read), so the run stays current - and its
    final snapshot survives once the jobs are purged. No-op if the run has no jobs.

    For a **staged** run this also drives the rollout: when the current wave finishes
    cleanly the next wave is released; if its failures exceed the threshold the run
    auto-pauses. Both result-posts and status polls call this, so a stalled wave (an
    agent that never reports) still advances on the next poll. A manually paused/cancelled
    run is left alone (advance_waves honors the stored control state)."""
    if not run_id:
        return None
    run = runs_repo.get(run_id)
    jobs = jobs_repo.list_by_run(tenant_id, run_id)
    if not jobs:
        return None
    agg = aggregate_run(jobs)
    if run and (run.get("wave_total") or 1) > 1:
        decision = advance_waves(run, jobs, agg)
        if decision["release_wave"] is not None:
            _release_wave(run_id, decision["release_wave"])
        runs_repo.set_counts(run_id, decision["state"], agg["counts"],
                             current_wave=decision["current_wave"])
        return {**agg, "state": decision["state"]}
    runs_repo.set_counts(run_id, agg["state"], agg["counts"])
    return agg


def handle_get_run(raw_token: str, run_id: str) -> dict:
    user = _verify_tenant_token(raw_token)
    if not user:
        return _err("unauthorized", 401)

    run = runs_repo.get(run_id)
    if not run or run.get("tenant_id") != user["tenant_id"]:
        return _err("run not found", 404)

    # Access: a fleet run is gated once against the fleet (members are ephemeral); a tag
    # run is filtered to the agents the user can see (best-effort once jobs are purged).
    fleet_id = run.get("fleet_id")
    if fleet_id:
        fleet = fleets_repo.get(fleet_id)
        if not fleet or fleet.get("tenant_id") != user["tenant_id"] or not can_access_fleet(user, fleet):
            return _err("run not found", 404)

    # Authoritative state (+ wave advancement) is computed over the full job set.
    agg = refresh_run(user["tenant_id"], run_id)
    run = runs_repo.get(run_id) or run   # re-read: refresh may have advanced current_wave

    jobs = jobs_repo.list_by_run(user["tenant_id"], run_id)
    if not fleet_id and jobs:
        _cache: dict = {}
        def _accessible(aid: str) -> bool:
            if aid not in _cache:
                a = agents_repo.get(aid)
                _cache[aid] = a is not None and can_access_agent(user, a)
            return _cache[aid]
        jobs = [j for j in jobs if _accessible(j["agent_id"])]
        if not jobs:
            return _err("run not found", 404)

    if agg is not None:
        # Live: failures drill-down from the (access-scoped) member jobs.
        failures = [
            {"agent_id": j["agent_id"], "status": j.get("status"), "exit_code": j.get("exit_code"),
             "stderr": (j.get("stderr") or "")[:_STDERR_SNIPPET]}
            for j in jobs if _failed(j)
        ][:_MAX_FAILURES]
    else:
        # Jobs purged: fall back to the run row's final cached snapshot.
        state = run.get("state")
        agg = {"state": state, "counts": run.get("counts") or {},
               "total": run.get("dispatched") or 0, "terminal": state in _TERMINAL_STATES}
        failures = []

    staged = sum(1 for j in jobs if j.get("status") == "HELD")

    return _ok({
        "run_id": run_id,
        "fleet_id": fleet_id,
        "tag": run.get("tag"),
        "command": run.get("command"),
        "created_by": run.get("created_by"),
        "created_at": run.get("created_at"),
        "dispatched": run.get("dispatched"),
        "skipped_count": run.get("skipped_count"),
        "skipped": run.get("skipped") or [],   # bounded [{agent_id, hostname, reason}]
        # Staged rollout progress (wave_total 1 / null rollout = not staged).
        "rollout": run.get("rollout"),
        "current_wave": run.get("current_wave") or 0,
        "wave_total": run.get("wave_total") or 1,
        "staged": staged,               # jobs still HELD (later waves not yet released)
        **agg,                          # state, counts, total, terminal
        "failures": failures,           # bounded drill-down (<= _MAX_FAILURES)
    })


def get_run_handler(event, context):
    logger.info("GET /tenant/runs/{run_id}")
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    run_id = (event.get("pathParameters") or {}).get("run_id", "")
    return handle_get_run(token, run_id)
