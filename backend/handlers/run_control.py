"""Run control: pause / resume / cancel a staged (waved) fan-out.

Only a staged run (wave_total > 1) has anything to control - a single-wave run holds no
later waves. Access mirrors fan-out: a fleet run needs write access to the fleet; a tag
run is limited to its creator for a restricted user. Usable with an API token (these are
CLI/MCP operations), so we authenticate with _verify_tenant_token like the fan-out.

  pause   - stop auto-releasing waves; in-flight jobs finish, later waves stay HELD.
  resume  - release the next wave and go back to running.
  cancel  - CANCEL every not-yet-released (HELD) wave; in-flight jobs finish.
"""
import logging

import shared.audit as audit
from shared.access import can_write_fleet, is_agent_restricted
from shared.auth import _bearer, _verify_tenant_token
from shared.response import _err, _ok
from shared.store import fleets_repo, jobs_repo, runs_repo
from handlers.runs import refresh_run, _release_wave

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# States from which no control action makes sense (already finished / cancelled).
_DONE_STATES = {"succeeded", "partial", "failed", "empty", "canceled"}


def _load_run_for_write(raw_token: str, run_id: str):
    """Auth + access-gate a run for a control action. Returns (user, run, None) on
    success or (None, None, error_response). 404 (not 403) on access failure, so a run's
    existence isn't leaked."""
    user = _verify_tenant_token(raw_token)
    if not user:
        return None, None, _err("unauthorized", 401)
    run = runs_repo.get(run_id)
    if not run or run.get("tenant_id") != user["tenant_id"]:
        return None, None, _err("run not found", 404)
    fleet_id = run.get("fleet_id")
    if fleet_id:
        fleet = fleets_repo.get(fleet_id)
        if not fleet or fleet.get("tenant_id") != user["tenant_id"] or not can_write_fleet(user, fleet):
            return None, None, _err("run not found", 404)
    elif is_agent_restricted(user) and run.get("created_by") != user["user_id"]:
        return None, None, _err("run not found", 404)
    return user, run, None


def _audit(user, action, run_id, ip, **meta):
    audit.write(action, tenant_id=user["tenant_id"], actor_id=user["user_id"],
                actor_name=user.get("username"), actor_role=user.get("role"),
                resource_type="run", resource_id=run_id, metadata=meta or None, ip_address=ip)


def handle_pause_run(run_id: str, raw_token: str, ip: str = "") -> dict:
    user, run, err = _load_run_for_write(raw_token, run_id)
    if err:
        return err
    if (run.get("wave_total") or 1) <= 1:
        return _err("run is not staged (no waves to pause)", 409)
    if run.get("state") not in ("running", "pending"):
        return _err(f"run cannot be paused from state '{run.get('state')}'", 409)
    runs_repo.set_state(run_id, "paused")
    _audit(user, "run.paused", run_id, ip)
    return _ok({"run_id": run_id, "state": "paused",
                "current_wave": run.get("current_wave") or 0,
                "wave_total": run.get("wave_total") or 1})


def handle_resume_run(run_id: str, raw_token: str, ip: str = "") -> dict:
    user, run, err = _load_run_for_write(raw_token, run_id)
    if err:
        return err
    if run.get("state") != "paused":
        return _err("run is not paused", 409)
    cw = run.get("current_wave") or 0
    wt = run.get("wave_total") or 1
    next_wave = cw + 1
    if next_wave < wt:
        _release_wave(run_id, next_wave)
        runs_repo.set_counts(run_id, "running", run.get("counts") or {}, current_wave=next_wave)
    else:
        # Paused on the last wave (edge): nothing to release, just clear the pause.
        runs_repo.set_state(run_id, "running")
    agg = refresh_run(user["tenant_id"], run_id)
    run = runs_repo.get(run_id) or run
    _audit(user, "run.resumed", run_id, ip, wave=run.get("current_wave") or 0)
    return _ok({"run_id": run_id, "state": (agg or {}).get("state", "running"),
                "current_wave": run.get("current_wave") or 0, "wave_total": wt})


def handle_cancel_run(run_id: str, raw_token: str, ip: str = "") -> dict:
    user, run, err = _load_run_for_write(raw_token, run_id)
    if err:
        return err
    if run.get("state") in _DONE_STATES:
        return _err(f"run is already {run.get('state')}", 409)
    canceled = jobs_repo.cancel_staged(run_id)
    runs_repo.set_state(run_id, "canceled")
    _audit(user, "run.canceled", run_id, ip, canceled=canceled)
    return _ok({"run_id": run_id, "state": "canceled", "canceled": canceled})


# --- Lambda entrypoints ------------------------------------------------------

def _run_id(event) -> str:
    return (event.get("pathParameters") or {}).get("run_id", "")


def _ip(event) -> str:
    return ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "")


def pause_run_handler(event, context):
    run_id = _run_id(event)
    logger.info("POST /tenant/runs/%s/pause", run_id)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_pause_run(run_id, token, _ip(event))


def resume_run_handler(event, context):
    run_id = _run_id(event)
    logger.info("POST /tenant/runs/%s/resume", run_id)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_resume_run(run_id, token, _ip(event))


def cancel_run_handler(event, context):
    run_id = _run_id(event)
    logger.info("POST /tenant/runs/%s/cancel", run_id)
    token = _bearer(event)
    if not token:
        return _err("missing Authorization header", 401)
    return handle_cancel_run(run_id, token, _ip(event))
