"""Temporary wild mode: an agent (or a fleet) can be put in `wild` for a bounded window and
then auto-reverts to a safer mode.

Two layers apply the revert, no always-on scheduler needed:
  * Lazily, wherever the mode is read - at job creation / fan-out (enforcement: no wild job
    runs past the window), on agent+fleet get/list (display), and on fleet claim (a new
    member inherits the reverted mode). This is the safety guarantee.
  * An hourly sweep in the heartbeat check (scan_expired_wild -> revert_expired_*) that
    converges the store and records `*.mode_reverted` near the real expiry, so an agent or
    fleet nobody happens to read doesn't linger as a phantom `wild` row in queries/exports.
Both entry points are idempotent.

The fleet is the policy source its members inherit, so a fleet's schedule is mirrored onto
its members on propagation; reverting a fleet re-propagates (clearing the members' schedule
and mode), and each member is independently covered by the agent-level revert for good measure.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import shared.audit as audit
from shared.store import agents_repo, fleets_repo

logger = logging.getLogger()


def mode_duration_expiry(duration: str) -> Tuple[bool, Optional[str]]:
    """Parse a temporary-wild duration into (ok, expires_iso). '' / 'permanent' -> (True,
    None) = permanent. '<n>h' / '<n>d' / '<n>w' -> a UTC expiry (capped at 30 days)."""
    d = (duration or "").strip().lower()
    if not d or d == "permanent":
        return True, None
    m = re.fullmatch(r"(\d+)(h|d|w)", d)
    if not m:
        return False, None
    n, unit = int(m.group(1)), m.group(2)
    secs = n * (3600 if unit == "h" else 86400 if unit == "d" else 604800)
    if secs <= 0 or secs > 30 * 86400:   # sane ceiling on temporary wild
        return False, None
    return True, (datetime.now(timezone.utc) + timedelta(seconds=secs)).isoformat()


def _window_elapsed(record: dict) -> Optional[str]:
    """Return the fallback mode if `record` is in a temporary wild window that has elapsed,
    else None (not wild, permanent, or still open). Shared by the agent + fleet reverts."""
    if not record or record.get("mode") != "wild":
        return None
    expires_at = record.get("mode_expires_at")
    if not expires_at:
        return None  # permanent wild - nothing scheduled
    # Both timestamps are tz-aware UTC isoformat, so a string compare is correct.
    if datetime.now(timezone.utc).isoformat() < expires_at:
        return None  # window still open
    return record.get("mode_revert_to") or "readonly"


def revert_expired_fleet_mode(fleet: dict) -> dict:
    """If `fleet` is in a temporary `wild` window that has elapsed, revert it to its
    scheduled fallback - persist, re-propagate the mode to every member (clearing their
    schedule too), and audit `fleet.mode_reverted`. Idempotent; cheap when nothing is due."""
    revert_to = _window_elapsed(fleet)
    if revert_to is None:
        return fleet
    fleets_repo.update_settings(fleet["fleet_id"],
                                {"mode": revert_to, "mode_expires_at": None, "mode_revert_to": None})
    agents_repo.set_mode_by_fleet(fleet["fleet_id"], revert_to,
                                  mode_expires_at=None, mode_revert_to=None)
    audit.write(
        "fleet.mode_reverted",
        tenant_id=fleet.get("tenant_id", ""),
        actor_id="system",
        actor_name="system",
        actor_role="system",
        resource_type="fleet",
        resource_id=fleet["fleet_id"],
        metadata={"from_mode": "wild", "to_mode": revert_to,
                  "name": fleet.get("name"), "reason": "temporary wild mode expired"},
    )
    logger.info("Reverted fleet=%s wild->%s (temporary wild mode expired)", fleet["fleet_id"], revert_to)
    return {**fleet, "mode": revert_to, "mode_expires_at": None, "mode_revert_to": None}


def revert_expired_mode(agent: dict) -> dict:
    """If `agent` is in a temporary `wild` mode whose window has elapsed, revert it to its
    scheduled fallback (persist + audit) and return the updated agent. Idempotent; cheap
    when there is nothing to revert (the common case)."""
    revert_to = _window_elapsed(agent)
    if revert_to is None:
        return agent
    agents_repo.update_policy(agent["agent_id"], revert_to, mode_expires_at=None, mode_revert_to=None)
    audit.write(
        "agent.mode_reverted",
        tenant_id=agent.get("tenant_id", ""),
        actor_id="system",
        actor_name="system",
        actor_role="system",
        resource_type="agent",
        resource_id=agent["agent_id"],
        metadata={"from_mode": "wild", "to_mode": revert_to,
                  "hostname": agent.get("hostname"), "reason": "temporary wild mode expired"},
    )
    logger.info("Reverted agent=%s wild->%s (temporary wild mode expired)", agent["agent_id"], revert_to)
    return {**agent, "mode": revert_to, "mode_expires_at": None, "mode_revert_to": None}
