import logging
from datetime import datetime, timezone

from shared.response import _now
from shared.store import agents_repo

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_heartbeat_check() -> int:
    cutoff_iso = datetime.fromtimestamp(_now() - 300, tz=timezone.utc).isoformat()
    marked = 0
    for agent in agents_repo.scan_stale_active(cutoff_iso):
        if agents_repo.mark_inactive(agent["agent_id"]):
            logger.info(
                "Marked agent %s INACTIVE (last_heartbeat_at=%s)",
                agent["agent_id"],
                agent.get("last_heartbeat_at"),
            )
            marked += 1
    return marked


def heartbeat_handler(event, context):
    marked = handle_heartbeat_check()
    logger.info("Heartbeat check complete: %d agent(s) marked INACTIVE", marked)
    return {"marked_inactive": marked}
