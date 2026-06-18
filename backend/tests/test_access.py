import pytest
from shared.access import can_access_agent


def _user(tenant_id="t1", allowed_agent_ids=None, allowed_fleet_ids=None):
    u = {"tenant_id": tenant_id}
    if allowed_agent_ids is not None:
        u["allowed_agent_ids"] = allowed_agent_ids
    if allowed_fleet_ids is not None:
        u["allowed_fleet_ids"] = allowed_fleet_ids
    return u


def _agent(agent_id="agent_a", tenant_id="t1", fleet_id=None):
    a = {"agent_id": agent_id, "tenant_id": tenant_id}
    if fleet_id is not None:
        a["fleet_id"] = fleet_id
    return a


# ---------------------------------------------------------------------------
# Tenant boundary
# ---------------------------------------------------------------------------

def test_different_tenant_is_denied():
    assert not can_access_agent(_user(tenant_id="t1"), _agent(tenant_id="t2"))


def test_same_tenant_is_required():
    assert can_access_agent(_user(tenant_id="t1"), _agent(tenant_id="t1"))


# ---------------------------------------------------------------------------
# Unrestricted (both fields absent / None)
# ---------------------------------------------------------------------------

def test_both_fields_absent_is_unrestricted():
    assert can_access_agent(_user(), _agent())


def test_both_fields_none_is_unrestricted():
    u = {"tenant_id": "t1", "allowed_agent_ids": None, "allowed_fleet_ids": None}
    assert can_access_agent(u, _agent())


def test_wildcard_list_is_unrestricted():
    assert can_access_agent(_user(allowed_agent_ids=["*"]), _agent())


def test_wildcard_list_with_extra_entries_is_unrestricted():
    # ["*", "agent_a"] still means unrestricted
    assert can_access_agent(_user(allowed_agent_ids=["*", "agent_a"]), _agent(agent_id="agent_b"))


# ---------------------------------------------------------------------------
# Explicit allowlist
# ---------------------------------------------------------------------------

def test_agent_in_list_is_allowed():
    assert can_access_agent(_user(allowed_agent_ids=["agent_a"]), _agent(agent_id="agent_a"))


def test_agent_not_in_list_is_denied():
    assert not can_access_agent(_user(allowed_agent_ids=["agent_a"]), _agent(agent_id="agent_b"))


def test_empty_list_locks_out():
    assert not can_access_agent(_user(allowed_agent_ids=[]), _agent())


# ---------------------------------------------------------------------------
# Regression: empty list must NOT fall back to wildcard
# ---------------------------------------------------------------------------

def test_empty_list_does_not_become_wildcard():
    # [] or ["*"] evaluates to ["*"] in Python - this is the bug we fixed
    u = _user(allowed_agent_ids=[])
    assert not can_access_agent(u, _agent(agent_id="agent_a"))
    assert not can_access_agent(u, _agent(agent_id="agent_b"))


# ---------------------------------------------------------------------------
# Fleet access
# ---------------------------------------------------------------------------

def test_fleet_only_restriction_grants_fleet_member():
    u = _user(allowed_fleet_ids=["fleet_1"])
    assert can_access_agent(u, _agent(fleet_id="fleet_1"))


def test_fleet_only_restriction_denies_wrong_fleet():
    u = _user(allowed_fleet_ids=["fleet_1"])
    assert not can_access_agent(u, _agent(fleet_id="fleet_2"))


def test_fleet_only_restriction_denies_unfleeted_agent():
    u = _user(allowed_fleet_ids=["fleet_1"])
    assert not can_access_agent(u, _agent())  # agent has no fleet_id


def test_agent_list_and_fleet_are_ored():
    u = _user(allowed_agent_ids=["agent_a"], allowed_fleet_ids=["fleet_1"])
    assert can_access_agent(u, _agent(agent_id="agent_a"))       # via agent list
    assert can_access_agent(u, _agent(agent_id="agent_b", fleet_id="fleet_1"))  # via fleet
    assert not can_access_agent(u, _agent(agent_id="agent_c"))   # neither


# ---------------------------------------------------------------------------
# Regression: agent_ids=None + fleet restriction must not grant everything
# ---------------------------------------------------------------------------

def test_none_agent_ids_with_fleet_restriction_is_not_unrestricted():
    # allowed_agent_ids=None + allowed_fleet_ids=["fleet_1"]
    # should grant only fleet members, not all agents
    u = _user(allowed_fleet_ids=["fleet_1"])
    # One of the fields is set, so the "both absent" shortcut must NOT fire
    assert not can_access_agent(u, _agent(agent_id="agent_x"))           # no fleet
    assert can_access_agent(u, _agent(agent_id="agent_x", fleet_id="fleet_1"))


# ---------------------------------------------------------------------------
# Multiple agents in list
# ---------------------------------------------------------------------------

def test_multiple_agents_in_list():
    u = _user(allowed_agent_ids=["agent_a", "agent_b"])
    assert can_access_agent(u, _agent(agent_id="agent_a"))
    assert can_access_agent(u, _agent(agent_id="agent_b"))
    assert not can_access_agent(u, _agent(agent_id="agent_c"))
