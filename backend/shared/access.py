from typing import Optional


_WILDCARD = "*"


def can_access_agent(user: dict, agent: dict) -> bool:
    """Return True if the user is allowed to see/use this agent.

    Rules:
    - tenant boundary is always enforced first
    - both fields absent/None → unrestricted (default)
    - allowed_agent_ids == ["*"] → unrestricted (explicit)
    - allowed_agent_ids is a list → only agents in that list
    - allowed_fleet_ids is a list → also grants access to agents in those fleets
    - individual agent and fleet grants are OR'd
    """
    if agent.get("tenant_id") != user.get("tenant_id"):
        return False

    _raw_agents: Optional[list] = user.get("allowed_agent_ids")
    _raw_fleets: Optional[list] = user.get("allowed_fleet_ids")

    # Both absent → fully unrestricted
    if _raw_agents is None and _raw_fleets is None:
        return True

    # At least one field is set - evaluate explicitly
    allowed_agents: list = _raw_agents if _raw_agents is not None else []
    allowed_fleets: Optional[list] = _raw_fleets

    if _WILDCARD in allowed_agents:
        return True

    in_agent_list = agent["agent_id"] in set(allowed_agents)
    in_fleet = (
        allowed_fleets is not None
        and agent.get("fleet_id") is not None
        and agent.get("fleet_id") in set(allowed_fleets)
    )
    return in_agent_list or in_fleet


def is_agent_restricted(user: dict) -> bool:
    """True if the user is scoped to a subset of agents (not tenant-wide).

    Admins/operators/developers are all subject to the same rule: if either
    allowed_agent_ids or allowed_fleet_ids is set, they only see/act on that
    subset. Both absent → unrestricted (whole tenant).
    """
    return user.get("allowed_agent_ids") is not None or user.get("allowed_fleet_ids") is not None


def accessible_agent_ids(user: dict, agents: list) -> list:
    """The subset of agent IDs (from `agents`) this user may see/use."""
    return [a["agent_id"] for a in agents if can_access_agent(user, a)]
