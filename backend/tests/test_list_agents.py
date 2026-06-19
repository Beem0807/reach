import pytest
from unittest.mock import patch, MagicMock

from handlers.list_agents import handle_list_agents
from handlers.get_agent import handle_get_agent


TENANT = "tenant_1"
USER = {"user_id": "user_1", "tenant_id": TENANT}

AGENT_A = {"agent_id": "agent_a", "tenant_id": TENANT, "status": "ACTIVE",
           "hostname": "host-a", "agent_version": "0.1", "claimed_at": None,
           "mode": "wild", "tags": ["env:prod"]}
AGENT_B = {"agent_id": "agent_b", "tenant_id": TENANT, "status": "ACTIVE",
           "hostname": "host-b", "agent_version": "0.1", "claimed_at": None,
           "mode": "readonly", "tags": []}


def _list(token="tok", agents=None, user=USER, tag=None):
    with patch("handlers.list_agents._verify_tenant_token", return_value=user), \
         patch("handlers.list_agents.agents_repo") as mock_repo:
        mock_repo.list_by_tenant.return_value = agents or []
        return handle_list_agents(token, tag)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_invalid_token_returns_401():
    with patch("handlers.list_agents._verify_tenant_token", return_value=None):
        r = handle_list_agents("bad-token")
    assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# Basic listing
# ---------------------------------------------------------------------------

def test_empty_tenant_returns_empty_list():
    r = _list(agents=[])
    assert r["statusCode"] == 200
    import json
    assert json.loads(r["body"])["agents"] == []


def test_accessible_agents_returned():
    r = _list(agents=[AGENT_A, AGENT_B])
    import json
    ids = [a["agent_id"] for a in json.loads(r["body"])["agents"]]
    assert "agent_a" in ids
    assert "agent_b" in ids


def test_agent_from_different_tenant_excluded():
    wrong_tenant_agent = {**AGENT_A, "tenant_id": "other_tenant"}
    r = _list(agents=[wrong_tenant_agent, AGENT_B])
    import json
    ids = [a["agent_id"] for a in json.loads(r["body"])["agents"]]
    assert "agent_a" not in ids
    assert "agent_b" in ids


# ---------------------------------------------------------------------------
# Access control filtering
# ---------------------------------------------------------------------------

def test_restricted_user_only_sees_allowed_agents():
    restricted_user = {**USER, "allowed_agent_ids": ["agent_b"]}
    r = _list(agents=[AGENT_A, AGENT_B], user=restricted_user)
    import json
    ids = [a["agent_id"] for a in json.loads(r["body"])["agents"]]
    assert "agent_a" not in ids
    assert "agent_b" in ids


def test_locked_out_user_sees_no_agents():
    locked_user = {**USER, "allowed_agent_ids": []}
    r = _list(agents=[AGENT_A, AGENT_B], user=locked_user)
    import json
    assert json.loads(r["body"])["agents"] == []


def test_wildcard_user_sees_all_agents():
    wildcard_user = {**USER, "allowed_agent_ids": ["*"]}
    r = _list(agents=[AGENT_A, AGENT_B], user=wildcard_user)
    import json
    ids = [a["agent_id"] for a in json.loads(r["body"])["agents"]]
    assert len(ids) == 2


# ---------------------------------------------------------------------------
# Tag filter
# ---------------------------------------------------------------------------

def test_tag_filter_returns_matching_agents():
    r = _list(agents=[AGENT_A, AGENT_B], tag="env:prod")
    import json
    ids = [a["agent_id"] for a in json.loads(r["body"])["agents"]]
    assert "agent_a" in ids
    assert "agent_b" not in ids


def test_tag_filter_no_match_returns_empty():
    r = _list(agents=[AGENT_A, AGENT_B], tag="env:staging")
    import json
    assert json.loads(r["body"])["agents"] == []


def test_no_tag_filter_returns_all_accessible():
    r = _list(agents=[AGENT_A, AGENT_B], tag=None)
    import json
    assert len(json.loads(r["body"])["agents"]) == 2


def test_tag_filter_combined_with_access_control():
    # User can only see agent_b, which has no tags - tag filter should return nothing
    restricted_user = {**USER, "allowed_agent_ids": ["agent_b"]}
    r = _list(agents=[AGENT_A, AGENT_B], user=restricted_user, tag="env:prod")
    import json
    assert json.loads(r["body"])["agents"] == []


# ---------------------------------------------------------------------------
# Response includes tags field
# ---------------------------------------------------------------------------

def test_response_includes_tags():
    r = _list(agents=[AGENT_A])
    import json
    agent = json.loads(r["body"])["agents"][0]
    assert "tags" in agent
    assert agent["tags"] == ["env:prod"]


def test_agent_with_no_tags_returns_empty_list():
    r = _list(agents=[AGENT_B])
    import json
    agent = json.loads(r["body"])["agents"][0]
    assert agent["tags"] == []


def test_deleted_agent_excluded_from_list():
    deleted = {**AGENT_A, "status": "DELETED"}
    r = _list(agents=[deleted, AGENT_B])
    import json
    ids = [a["agent_id"] for a in json.loads(r["body"])["agents"]]
    assert "agent_a" not in ids
    assert "agent_b" in ids


def test_revoked_agent_included_in_list():
    revoked = {**AGENT_A, "status": "REVOKED"}
    r = _list(agents=[revoked, AGENT_B])
    import json
    ids = [a["agent_id"] for a in json.loads(r["body"])["agents"]]
    assert "agent_a" in ids


# ---------------------------------------------------------------------------
# get_agent: 404 on no access (info-leak prevention)
# ---------------------------------------------------------------------------

def test_get_agent_unauthorized():
    with patch("handlers.get_agent._verify_tenant_token", return_value=None):
        r = handle_get_agent("agent_a", "bad-token")
    assert r["statusCode"] == 401


def test_get_agent_returns_404_when_user_has_no_access():
    restricted_user = {**USER, "allowed_agent_ids": ["agent_b"]}
    with patch("handlers.get_agent._verify_tenant_token", return_value=restricted_user), \
         patch("handlers.get_agent.agents_repo") as mock_repo:
        mock_repo.get.return_value = AGENT_A
        r = handle_get_agent("agent_a", "tok")
    assert r["statusCode"] == 404


def test_get_agent_returns_200_when_user_has_access():
    with patch("handlers.get_agent._verify_tenant_token", return_value=USER), \
         patch("handlers.get_agent.agents_repo") as mock_repo:
        mock_repo.get.return_value = AGENT_A
        r = handle_get_agent("agent_a", "tok")
    assert r["statusCode"] == 200


def test_get_agent_returns_tags():
    with patch("handlers.get_agent._verify_tenant_token", return_value=USER), \
         patch("handlers.get_agent.agents_repo") as mock_repo:
        mock_repo.get.return_value = AGENT_A
        r = handle_get_agent("agent_a", "tok")
    import json
    assert json.loads(r["body"])["tags"] == ["env:prod"]


def test_get_deleted_agent_returns_404():
    deleted = {**AGENT_A, "status": "DELETED"}
    with patch("handlers.get_agent._verify_tenant_token", return_value=USER), \
         patch("handlers.get_agent.agents_repo") as mock_repo:
        mock_repo.get.return_value = deleted
        r = handle_get_agent("agent_a", "tok")
    assert r["statusCode"] == 404


def test_get_revoked_agent_returns_200():
    revoked = {**AGENT_A, "status": "REVOKED"}
    with patch("handlers.get_agent._verify_tenant_token", return_value=USER), \
         patch("handlers.get_agent.agents_repo") as mock_repo:
        mock_repo.get.return_value = revoked
        r = handle_get_agent("agent_a", "tok")
    assert r["statusCode"] == 200
