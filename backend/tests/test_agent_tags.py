"""Tests for tenant-scoped agent tag management (handlers.tenant_agents)."""
import json
import pytest
from unittest.mock import patch, MagicMock

from handlers.tenant_agents import handle_set_tenant_agent_tags

TENANT = "tenant_1"
AGENT_ID = "agent_a"
TOKEN = "tok_test"

_USER_OPERATOR = {"user_id": "u1", "tenant_id": TENANT, "role": "operator"}
_USER_DEVELOPER = {"user_id": "u2", "tenant_id": TENANT, "role": "developer"}
_AGENT_NO_TAGS = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE", "hostname": "host", "agent_version": "0.1"}
_AGENT_WITH_TAGS = {**_AGENT_NO_TAGS, "tags": ["env:prod", "region:us-east-1"]}


def _auth(user=_USER_OPERATOR):
    return patch("handlers.tenant_agents._verify_tenant_token", return_value=user)


class TestSetTenantAgentTags:
    def test_unauthorized(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=None):
            r = handle_set_tenant_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, TOKEN)
        assert r["statusCode"] == 401

    def test_developer_forbidden(self):
        with _auth(_USER_DEVELOPER), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_NO_TAGS
            r = handle_set_tenant_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, TOKEN)
        assert r["statusCode"] == 403

    def test_agent_not_found(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_set_tenant_agent_tags(AGENT_ID, {"tags": ["env:prod"]}, TOKEN)
        assert r["statusCode"] == 404

    def test_invalid_tags_rejected(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_NO_TAGS
            r = handle_set_tenant_agent_tags(AGENT_ID, {"tags": ["INVALID"]}, TOKEN)
        assert r["statusCode"] == 400

    def test_valid_tags_stored(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_WITH_TAGS
            r = handle_set_tenant_agent_tags(AGENT_ID, {"tags": ["env:staging"]}, TOKEN)
        assert r["statusCode"] == 200
        ar.set_tags.assert_called_once_with(AGENT_ID, ["env:staging"])

    def test_empty_list_clears_tags(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_WITH_TAGS
            r = handle_set_tenant_agent_tags(AGENT_ID, {"tags": []}, TOKEN)
        assert r["statusCode"] == 200
        ar.set_tags.assert_called_once_with(AGENT_ID, [])
        assert json.loads(r["body"])["tags"] == []
