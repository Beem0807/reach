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
    restricted_user = {**USER, "readwrite_agent_ids": ["agent_b"]}
    r = _list(agents=[AGENT_A, AGENT_B], user=restricted_user)
    import json
    ids = [a["agent_id"] for a in json.loads(r["body"])["agents"]]
    assert "agent_a" not in ids
    assert "agent_b" in ids


def test_locked_out_user_sees_no_agents():
    locked_user = {**USER, "readwrite_agent_ids": []}
    r = _list(agents=[AGENT_A, AGENT_B], user=locked_user)
    import json
    assert json.loads(r["body"])["agents"] == []


def test_user_sees_only_explicitly_listed_agents():
    # No wildcard: "all agents" is the full set of ids, listed explicitly.
    scoped = {**USER, "readwrite_agent_ids": [AGENT_A["agent_id"], AGENT_B["agent_id"]]}
    r = _list(agents=[AGENT_A, AGENT_B], user=scoped)
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
    restricted_user = {**USER, "readwrite_agent_ids": ["agent_b"]}
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


def test_all_tags_facet_spans_every_accessible_agent():
    import json
    fleet_member = {**AGENT_A, "agent_id": "agent_f", "fleet_id": "fleet_1",
                    "tags": ["env:prod", "fleet-only"]}
    standalone = {**AGENT_A, "agent_id": "agent_s", "fleet_id": None, "tags": ["env:prod", "solo"]}
    with patch("handlers.list_agents._verify_tenant_token", return_value=USER), \
         patch("handlers.list_agents.agents_repo") as mock_repo:
        mock_repo.list_by_tenant.return_value = [fleet_member, standalone]
        r = handle_list_agents("tok", limit=50)
    body = json.loads(r["body"])
    assert body["all_tags"] == ["env:prod", "fleet-only", "solo"]


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
    restricted_user = {**USER, "readwrite_agent_ids": ["agent_b"]}
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


# ---------------------------------------------------------------------------
# Strict grant-mismatch acceptance: lazy-clear an exception when the member
# now matches its fleet, so a later return to the same divergence re-flags.
# ---------------------------------------------------------------------------

_FLEET_WANTS_DOCKER = {"fleet_id": "fleet_1", "tenant_id": TENANT,
                       "grant_service_mgmt": False, "grant_docker": True}


def _member(exc, sm, dk):
    return {"agent_id": "m1", "tenant_id": TENANT, "status": "ACTIVE", "hostname": "m1",
            "mode": "approved", "tags": [], "fleet_id": "fleet_1",
            "grant_service_mgmt": sm, "grant_docker": dk, "grants_exception": exc}


def _list_with_fleets(members):
    with patch("handlers.list_agents._verify_tenant_token", return_value=USER), \
         patch("handlers.list_agents.agents_repo") as ar, \
         patch("handlers.list_agents.fleets_repo") as fr:
        ar.list_by_tenant.return_value = members
        fr.list_by_tenant.return_value = [_FLEET_WANTS_DOCKER]
        r = handle_list_agents("tok")
        return r, ar


def test_exception_cleared_when_member_now_matches_fleet():
    # Member carries an exception but its grants now equal the fleet's (docker on) -> the
    # exception is dropped, so the member returns to normal (strict acceptance).
    r, ar = _list_with_fleets([_member("00-01", False, True)])
    ar.set_grants_exception.assert_called_once_with("m1", None)
    import json
    assert json.loads(r["body"])["agents"][0]["grants_exception"] is None


def test_exception_kept_while_member_still_diverges():
    # Still mismatched (docker off) -> the exception stays; not cleared.
    r, ar = _list_with_fleets([_member("00-01", False, False)])
    ar.set_grants_exception.assert_not_called()


def test_no_fleet_lookup_when_no_exceptions():
    # Fast path: no member carries an exception -> fleets_repo is never queried.
    with patch("handlers.list_agents._verify_tenant_token", return_value=USER), \
         patch("handlers.list_agents.agents_repo") as ar, \
         patch("handlers.list_agents.fleets_repo") as fr:
        ar.list_by_tenant.return_value = [_member(None, False, False)]
        handle_list_agents("tok")
        fr.list_by_tenant.assert_not_called()


# ---------------------------------------------------------------------------
# Search (q) + opt-in pagination (limit/offset, total)
# ---------------------------------------------------------------------------

def _big_tenant(n):
    return [{"agent_id": f"agent_{i:03d}", "tenant_id": TENANT, "status": "ACTIVE",
             "hostname": f"host-{i:03d}", "mode": "wild", "tags": (["env:prod"] if i % 2 == 0 else [])}
            for i in range(n)]


def _call(agents, q=None, limit=None, offset=0, tag=None, mode=None, access=None,
          agent_type=None, fleet=None):
    with patch("handlers.list_agents._verify_tenant_token", return_value=USER), \
         patch("handlers.list_agents.agents_repo") as ar:
        ar.list_by_tenant.return_value = agents
        return handle_list_agents("tok", tag, q=q, mode=mode, access=access,
                                  agent_type=agent_type, fleet=fleet, limit=limit, offset=offset)


def test_no_limit_returns_all_no_total():
    import json
    body = json.loads(_call(_big_tenant(30))["body"])
    assert len(body["agents"]) == 30
    assert "total" not in body   # backward compatible for CLI/MCP

def test_limit_paginates_with_total():
    import json
    body = json.loads(_call(_big_tenant(30), limit=20)["body"])
    assert len(body["agents"]) == 20 and body["total"] == 30
    assert body["limit"] == 20 and body["offset"] == 0

def test_offset_returns_next_page():
    import json
    body = json.loads(_call(_big_tenant(30), limit=20, offset=20)["body"])
    assert len(body["agents"]) == 10 and body["total"] == 30
    assert [a["agent_id"] for a in body["agents"]][0] == "agent_020"

def test_search_filters_before_pagination():
    import json
    # q matches one hostname; total reflects the filtered set, not the tenant size.
    body = json.loads(_call(_big_tenant(30), q="host-007", limit=20)["body"])
    assert body["total"] == 1 and body["agents"][0]["agent_id"] == "agent_007"

def test_search_matches_tags_and_id_case_insensitive():
    import json
    body = json.loads(_call(_big_tenant(10), q="ENV:PROD")["body"])
    assert len(body["agents"]) == 5           # even indices carry env:prod
    body2 = json.loads(_call(_big_tenant(10), q="AGENT_003")["body"])
    assert [a["agent_id"] for a in body2["agents"]] == ["agent_003"]

def test_limit_capped_at_100():
    import json
    body = json.loads(_call(_big_tenant(5), limit=9999)["body"])
    assert body["limit"] == 100


# ---------------------------------------------------------------------------
# Server-side dropdown filters (mode/access/type/fleet) + the all_tags facet
# ---------------------------------------------------------------------------

def _mixed_tenant():
    return [
        {"agent_id": "a1", "tenant_id": TENANT, "status": "ACTIVE", "hostname": "h1",
         "mode": "wild", "access_level": "open", "type": "host", "fleet_id": None, "tags": ["env:prod"]},
        {"agent_id": "a2", "tenant_id": TENANT, "status": "ACTIVE", "hostname": "h2",
         "mode": "approved", "access_level": "restricted", "type": "k8s", "fleet_id": "fleet_1", "tags": ["env:dev", "team:x"]},
        {"agent_id": "a3", "tenant_id": TENANT, "status": "ACTIVE", "hostname": "h3",
         "mode": "wild", "access_level": "open", "type": "host", "fleet_id": "fleet_1", "tags": ["env:prod", "team:y"]},
    ]

def test_filter_by_mode():
    import json
    body = json.loads(_call(_mixed_tenant(), mode="wild", limit=20)["body"])
    assert {a["agent_id"] for a in body["agents"]} == {"a1", "a3"}

def test_filter_by_access_level():
    import json
    body = json.loads(_call(_mixed_tenant(), access="restricted", limit=20)["body"])
    assert {a["agent_id"] for a in body["agents"]} == {"a2"}

def test_filter_by_type():
    import json
    body = json.loads(_call(_mixed_tenant(), agent_type="k8s", limit=20)["body"])
    assert {a["agent_id"] for a in body["agents"]} == {"a2"}

def test_filter_by_fleet():
    import json
    body = json.loads(_call(_mixed_tenant(), fleet="fleet_1", limit=20)["body"])
    assert {a["agent_id"] for a in body["agents"]} == {"a2", "a3"}

def test_filter_standalone_only():
    import json
    body = json.loads(_call(_mixed_tenant(), fleet="__none__", limit=20)["body"])
    assert {a["agent_id"] for a in body["agents"]} == {"a1"}

def test_multi_tag_or_semantics():
    import json
    body = json.loads(_call(_mixed_tenant(), tag="env:dev,team:y", limit=20)["body"])
    assert {a["agent_id"] for a in body["agents"]} == {"a2", "a3"}

def test_all_tags_facet_covers_full_set_not_page():
    import json
    # Even with a 1-agent page and an active filter, the facet lists every tenant tag.
    body = json.loads(_call(_mixed_tenant(), mode="wild", limit=1)["body"])
    assert body["all_tags"] == ["env:dev", "env:prod", "team:x", "team:y"]

def test_no_all_tags_facet_without_pagination():
    import json
    body = json.loads(_call(_mixed_tenant())["body"])  # CLI/MCP path
    assert "all_tags" not in body
