import json
import pytest
from unittest.mock import patch, call

from handlers.admin_agents import (
    handle_get_agent_tags,
    handle_set_agent_tags,
    handle_add_agent_tags,
    handle_remove_agent_tags,
    handle_list_agents_admin,
)

ADMIN = "test-admin-token"
TENANT = "tenant_1"
AGENT_ID = "agent_a"

_AGENT_NO_TAGS = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE",
                  "hostname": "host", "agent_version": "0.1", "claimed_at": None,
                  "mode": "wild"}
_AGENT_WITH_TAGS = {**_AGENT_NO_TAGS, "tags": ["env:prod", "region:us-east-1"]}
_AGENT_B = {**_AGENT_NO_TAGS, "agent_id": "agent_b", "tags": ["env:staging"]}


# ---------------------------------------------------------------------------
# handle_get_agent_tags
# ---------------------------------------------------------------------------

class TestGetAgentTags:
    def test_unauthorized(self):
        r = handle_get_agent_tags(AGENT_ID, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_get_agent_tags(AGENT_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_agent_with_no_tags_returns_empty_list(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_NO_TAGS
            r = handle_get_agent_tags(AGENT_ID, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["tags"] == []

    def test_agent_with_tags_returns_them(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_WITH_TAGS
            r = handle_get_agent_tags(AGENT_ID, ADMIN)
        assert json.loads(r["body"])["tags"] == ["env:prod", "region:us-east-1"]


# ---------------------------------------------------------------------------
# handle_set_agent_tags
# ---------------------------------------------------------------------------

class TestSetAgentTags:
    def test_unauthorized(self):
        r = handle_set_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_set_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, ADMIN)
        assert r["statusCode"] == 404

    def test_invalid_tags_rejected(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_NO_TAGS
            r = handle_set_agent_tags(AGENT_ID, {"tags": ["INVALID"]}, ADMIN)
        assert r["statusCode"] == 400

    def test_valid_tags_stored(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_WITH_TAGS
            r = handle_set_agent_tags(AGENT_ID, {"tags": ["env:staging"]}, ADMIN)
        assert r["statusCode"] == 200
        ar.set_tags.assert_called_once_with(AGENT_ID, ["env:staging"])

    def test_empty_list_clears_tags(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_WITH_TAGS
            r = handle_set_agent_tags(AGENT_ID, {"tags": []}, ADMIN)
        assert r["statusCode"] == 200
        ar.set_tags.assert_called_once_with(AGENT_ID, [])
        assert json.loads(r["body"])["tags"] == []


# ---------------------------------------------------------------------------
# handle_add_agent_tags
# ---------------------------------------------------------------------------

class TestAddAgentTags:
    def test_unauthorized(self):
        r = handle_add_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, "wrong")
        assert r["statusCode"] == 401

    def test_invalid_tags_rejected(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_NO_TAGS
            r = handle_add_agent_tags(AGENT_ID, {"tags": ["BAD TAG"]}, ADMIN)
        assert r["statusCode"] == 400

    def test_new_tags_merged_with_existing(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_WITH_TAGS  # has ["env:prod", "region:us-east-1"]
            r = handle_add_agent_tags(AGENT_ID, {"tags": ["team:infra"]}, ADMIN)
        assert r["statusCode"] == 200
        stored = ar.set_tags.call_args[0][1]
        assert set(stored) == {"env:prod", "region:us-east-1", "team:infra"}

    def test_duplicate_tags_not_added_twice(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_WITH_TAGS  # already has "env:prod"
            r = handle_add_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, ADMIN)
        assert r["statusCode"] == 200
        stored = ar.set_tags.call_args[0][1]
        assert stored.count("env:prod") == 1

    def test_agent_not_found(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_add_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, ADMIN)
        assert r["statusCode"] == 404

    def test_add_to_empty_tags(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_NO_TAGS
            r = handle_add_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, ADMIN)
        assert r["statusCode"] == 200
        stored = ar.set_tags.call_args[0][1]
        assert "env:prod" in stored


# ---------------------------------------------------------------------------
# handle_remove_agent_tags
# ---------------------------------------------------------------------------

class TestRemoveAgentTags:
    def test_unauthorized(self):
        r = handle_remove_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_remove_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, ADMIN)
        assert r["statusCode"] == 404

    def test_remove_existing_tag(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_WITH_TAGS  # ["env:prod", "region:us-east-1"]
            r = handle_remove_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, ADMIN)
        assert r["statusCode"] == 200
        stored = ar.set_tags.call_args[0][1]
        assert "env:prod" not in stored
        assert "region:us-east-1" in stored

    def test_remove_nonexistent_tag_is_silent(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_WITH_TAGS
            r = handle_remove_agent_tags(AGENT_ID, {"tags": ["team:infra"]}, ADMIN)
        assert r["statusCode"] == 200
        stored = ar.set_tags.call_args[0][1]
        assert stored == ["env:prod", "region:us-east-1"]

    def test_remove_all_tags(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_WITH_TAGS
            r = handle_remove_agent_tags(AGENT_ID, {"tags": ["env:prod", "region:us-east-1"]}, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["tags"] == []


# ---------------------------------------------------------------------------
# handle_list_agents_admin: tag filter
# ---------------------------------------------------------------------------

class TestListAgentsAdminTagFilter:
    def _call(self, tag=None):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.tenants_repo") as tr:
            tr.get.return_value = {"tenant_id": TENANT}
            ar.list_by_tenant.return_value = [_AGENT_WITH_TAGS, _AGENT_B]
            return handle_list_agents_admin(TENANT, ADMIN, tag)

    def test_no_filter_returns_all(self):
        r = self._call()
        agents = json.loads(r["body"])["agents"]
        ids = [a["agent_id"] for a in agents]
        assert "agent_a" in ids
        assert "agent_b" in ids

    def test_tag_filter_returns_matching_only(self):
        r = self._call(tag="env:prod")
        agents = json.loads(r["body"])["agents"]
        ids = [a["agent_id"] for a in agents]
        assert "agent_a" in ids
        assert "agent_b" not in ids

    def test_tag_filter_no_match_returns_empty(self):
        r = self._call(tag="env:canary")
        assert json.loads(r["body"])["agents"] == []

    def test_tags_included_in_response(self):
        r = self._call()
        agents = {a["agent_id"]: a for a in json.loads(r["body"])["agents"]}
        assert agents["agent_a"]["tags"] == ["env:prod", "region:us-east-1"]
        assert agents["agent_b"]["tags"] == ["env:staging"]

    def test_missing_tenant_id_returns_400(self):
        r = handle_list_agents_admin("", ADMIN)
        assert r["statusCode"] == 400

    def test_tenant_not_found_returns_404(self):
        with patch("handlers.admin_agents.agents_repo"), \
             patch("handlers.admin_agents.tenants_repo") as tr:
            tr.get.return_value = None
            r = handle_list_agents_admin(TENANT, ADMIN)
        assert r["statusCode"] == 404

    def test_unauthorized(self):
        r = handle_list_agents_admin(TENANT, "wrong")
        assert r["statusCode"] == 401
