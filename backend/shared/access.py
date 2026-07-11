from typing import Optional


def _in(value, ids: Optional[list]) -> bool:
    return ids is not None and value is not None and value in set(ids)


def can_access_agent(user: dict, agent: dict) -> bool:
    """Return True if the user is allowed to see/use (**read**) this agent.

    Access is scoped by four optional lists on the user record, partitioned by
    capability - `readwrite_*` are read-write grants, `readonly_*` are read-only
    grants - and read access is the **union** of all four:

    - tenant boundary is always enforced first
    - all four fields absent/None → unrestricted (**admins only** - non-admins always
      carry explicit lists). There is no wildcard: "all agents" for a non-admin is the
      full set of agent ids listed explicitly.
    - agent listed in readwrite_agent_ids OR readonly_agent_ids → access
    - agent's fleet in readwrite_fleet_ids OR readonly_fleet_ids → access
    - a restricted user with no matching grant → no access (default deny)
    """
    if agent.get("tenant_id") != user.get("tenant_id"):
        return False

    allowed_agents = user.get("readwrite_agent_ids")
    allowed_fleets = user.get("readwrite_fleet_ids")
    ro_agents = user.get("readonly_agent_ids")
    ro_fleets = user.get("readonly_fleet_ids")

    # No list set at all → fully unrestricted (admins / explicit tenant-wide).
    if allowed_agents is None and allowed_fleets is None and ro_agents is None and ro_fleets is None:
        return True

    agent_id = agent.get("agent_id")
    fleet_id = agent.get("fleet_id")
    return (
        _in(agent_id, allowed_agents)
        or _in(agent_id, ro_agents)
        or _in(fleet_id, allowed_fleets)
        or _in(fleet_id, ro_fleets)
    )


def can_write_agent(user: dict, agent: dict) -> bool:
    """Return True if the user may submit **write** commands to this agent.

    Write access is only the `readwrite_*` grants - agents in the `readonly_*` lists
    are read-only for this user. This only ever *narrows*: it never bypasses the
    agent's policy mode (which still gates the write), it just stops this user from
    attempting a write at all, in any mode. If an id somehow appears in both a
    read-write and a read-only list, the read-write grant wins.
    """
    if agent.get("tenant_id") != user.get("tenant_id"):
        return False

    allowed_agents = user.get("readwrite_agent_ids")
    allowed_fleets = user.get("readwrite_fleet_ids")
    ro_agents = user.get("readonly_agent_ids")
    ro_fleets = user.get("readonly_fleet_ids")

    # No list set at all → unrestricted read-write (admins).
    if allowed_agents is None and allowed_fleets is None and ro_agents is None and ro_fleets is None:
        return True

    return _in(agent.get("agent_id"), allowed_agents) or _in(agent.get("fleet_id"), allowed_fleets)


def can_access_fleet(user: dict, fleet: dict) -> bool:
    """Read access to a fleet: any grant on it (read-write or read-only), or admin."""
    if fleet.get("tenant_id") != user.get("tenant_id"):
        return False
    rw_a = user.get("readwrite_agent_ids")
    rw_f = user.get("readwrite_fleet_ids")
    ro_a = user.get("readonly_agent_ids")
    ro_f = user.get("readonly_fleet_ids")
    if rw_a is None and rw_f is None and ro_a is None and ro_f is None:
        return True
    return _in(fleet.get("fleet_id"), rw_f) or _in(fleet.get("fleet_id"), ro_f)


def can_write_fleet(user: dict, fleet: dict) -> bool:
    """Read-write access to a fleet (needed to create/review its approvals), or admin."""
    if fleet.get("tenant_id") != user.get("tenant_id"):
        return False
    rw_a = user.get("readwrite_agent_ids")
    rw_f = user.get("readwrite_fleet_ids")
    ro_a = user.get("readonly_agent_ids")
    ro_f = user.get("readonly_fleet_ids")
    if rw_a is None and rw_f is None and ro_a is None and ro_f is None:
        return True
    return _in(fleet.get("fleet_id"), rw_f)


def is_agent_restricted(user: dict) -> bool:
    """True if the user is scoped to a subset of agents (not tenant-wide).

    Any of the four scope lists being set makes the user restricted; all absent →
    unrestricted (whole tenant).
    """
    return (
        user.get("readwrite_agent_ids") is not None
        or user.get("readwrite_fleet_ids") is not None
        or user.get("readonly_agent_ids") is not None
        or user.get("readonly_fleet_ids") is not None
    )


def accessible_agent_ids(user: dict, agents: list) -> list:
    """The subset of agent IDs (from `agents`) this user may see/use."""
    return [a["agent_id"] for a in agents if can_access_agent(user, a)]
