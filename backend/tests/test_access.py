import pytest
from shared.access import can_access_agent


def _user(tenant_id="t1", readwrite_agent_ids=None, readwrite_fleet_ids=None):
    u = {"tenant_id": tenant_id}
    if readwrite_agent_ids is not None:
        u["readwrite_agent_ids"] = readwrite_agent_ids
    if readwrite_fleet_ids is not None:
        u["readwrite_fleet_ids"] = readwrite_fleet_ids
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
    u = {"tenant_id": "t1", "readwrite_agent_ids": None, "readwrite_fleet_ids": None}
    assert can_access_agent(u, _agent())


def test_wildcard_is_a_literal_not_unrestricted():
    # No wildcard: "*" is just a (never-matching) literal id, not "all agents".
    assert not can_access_agent(_user(readwrite_agent_ids=["*"]), _agent(agent_id="agent_a"))


def test_only_listed_ids_grant_access():
    # "all agents" for a non-admin is the full set of ids listed explicitly.
    u = _user(readwrite_agent_ids=["agent_a", "agent_b"])
    assert can_access_agent(u, _agent(agent_id="agent_a"))
    assert not can_access_agent(u, _agent(agent_id="agent_c"))


# ---------------------------------------------------------------------------
# Explicit allowlist
# ---------------------------------------------------------------------------

def test_agent_in_list_is_allowed():
    assert can_access_agent(_user(readwrite_agent_ids=["agent_a"]), _agent(agent_id="agent_a"))


def test_agent_not_in_list_is_denied():
    assert not can_access_agent(_user(readwrite_agent_ids=["agent_a"]), _agent(agent_id="agent_b"))


def test_empty_list_locks_out():
    assert not can_access_agent(_user(readwrite_agent_ids=[]), _agent())


# ---------------------------------------------------------------------------
# Regression: empty list must NOT fall back to wildcard
# ---------------------------------------------------------------------------

def test_empty_list_does_not_become_wildcard():
    # [] or ["*"] evaluates to ["*"] in Python - this is the bug we fixed
    u = _user(readwrite_agent_ids=[])
    assert not can_access_agent(u, _agent(agent_id="agent_a"))
    assert not can_access_agent(u, _agent(agent_id="agent_b"))


# ---------------------------------------------------------------------------
# Fleet access
# ---------------------------------------------------------------------------

def test_fleet_only_restriction_grants_fleet_member():
    u = _user(readwrite_fleet_ids=["fleet_1"])
    assert can_access_agent(u, _agent(fleet_id="fleet_1"))


def test_fleet_only_restriction_denies_wrong_fleet():
    u = _user(readwrite_fleet_ids=["fleet_1"])
    assert not can_access_agent(u, _agent(fleet_id="fleet_2"))


def test_fleet_only_restriction_denies_unfleeted_agent():
    u = _user(readwrite_fleet_ids=["fleet_1"])
    assert not can_access_agent(u, _agent())  # agent has no fleet_id


def test_agent_list_and_fleet_are_ored():
    u = _user(readwrite_agent_ids=["agent_a"], readwrite_fleet_ids=["fleet_1"])
    assert can_access_agent(u, _agent(agent_id="agent_a"))       # via agent list
    assert can_access_agent(u, _agent(agent_id="agent_b", fleet_id="fleet_1"))  # via fleet
    assert not can_access_agent(u, _agent(agent_id="agent_c"))   # neither


# ---------------------------------------------------------------------------
# Regression: agent_ids=None + fleet restriction must not grant everything
# ---------------------------------------------------------------------------

def test_none_agent_ids_with_fleet_restriction_is_not_unrestricted():
    # readwrite_agent_ids=None + readwrite_fleet_ids=["fleet_1"]
    # should grant only fleet members, not all agents
    u = _user(readwrite_fleet_ids=["fleet_1"])
    # One of the fields is set, so the "both absent" shortcut must NOT fire
    assert not can_access_agent(u, _agent(agent_id="agent_x"))           # no fleet
    assert can_access_agent(u, _agent(agent_id="agent_x", fleet_id="fleet_1"))


# ---------------------------------------------------------------------------
# Multiple agents in list
# ---------------------------------------------------------------------------

def test_multiple_agents_in_list():
    u = _user(readwrite_agent_ids=["agent_a", "agent_b"])
    assert can_access_agent(u, _agent(agent_id="agent_a"))
    assert can_access_agent(u, _agent(agent_id="agent_b"))
    assert not can_access_agent(u, _agent(agent_id="agent_c"))


# ---------------------------------------------------------------------------
# Read-only grants: partition model (readwrite = write, readonly = read-only)
# ---------------------------------------------------------------------------

from shared.access import can_write_agent, is_agent_restricted


def _ruser(tenant_id="t1", **fields):
    return {"tenant_id": tenant_id, **fields}


def test_readonly_agent_grants_read_but_not_write():
    u = _ruser(readonly_agent_ids=["agent_a"])
    a = _agent(agent_id="agent_a")
    assert can_access_agent(u, a)          # read access
    assert not can_write_agent(u, a)       # but no write


def test_readwrite_agent_grants_both():
    u = _ruser(readwrite_agent_ids=["agent_a"])
    a = _agent(agent_id="agent_a")
    assert can_access_agent(u, a)
    assert can_write_agent(u, a)


def test_read_access_is_union_of_readwrite_and_readonly():
    u = _ruser(readwrite_agent_ids=["agent_a"], readonly_agent_ids=["agent_b"])
    assert can_access_agent(u, _agent(agent_id="agent_a"))
    assert can_access_agent(u, _agent(agent_id="agent_b"))
    assert not can_access_agent(u, _agent(agent_id="agent_c"))
    assert can_write_agent(u, _agent(agent_id="agent_a"))
    assert not can_write_agent(u, _agent(agent_id="agent_b"))


def test_unrestricted_user_can_write():
    assert can_write_agent(_ruser(), _agent())


def test_wildcard_is_not_write_all():
    # "*" is a literal id, so it grants write to nothing real.
    u = _ruser(readwrite_agent_ids=["*"])
    assert not can_write_agent(u, _agent(agent_id="agent_a"))


def test_readonly_fleet_grants_read_only_to_members():
    u = _ruser(readonly_fleet_ids=["fleet_1"])
    a = _agent(agent_id="agent_a", fleet_id="fleet_1")
    assert can_access_agent(u, a)
    assert not can_write_agent(u, a)


def test_no_write_to_inaccessible_agent():
    u = _ruser(readwrite_agent_ids=["agent_a"])
    assert not can_write_agent(u, _agent(agent_id="agent_z"))


def test_readwrite_wins_if_id_in_both_lists():
    # Defensive: allowed (read-write) takes precedence over a stray readonly entry.
    u = _ruser(readwrite_agent_ids=["agent_a"], readonly_agent_ids=["agent_a"])
    assert can_write_agent(u, _agent(agent_id="agent_a"))


def test_readonly_only_user_is_restricted():
    assert is_agent_restricted(_ruser(readonly_agent_ids=["agent_a"]))
    assert not is_agent_restricted(_ruser())
