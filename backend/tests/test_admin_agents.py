"""Tests for platform admin agent listing (handlers/admin_agents.py)."""
import json
from unittest.mock import patch

import conftest
from handlers.admin_agents import handle_list_agents_admin, list_agents_admin_handler

ADMIN = conftest.ADMIN_TOKEN
TENANT_ID = "tenant_acme"

_TENANT = {"tenant_id": TENANT_ID, "name": "Acme", "status": "ACTIVE"}

_AGENT_BASE = {
    "agent_id": "agent_1",
    "tenant_id": TENANT_ID,
    "status": "ACTIVE",
    "hostname": "host.local",
    "agent_version": "0.1",
    "mode": "wild",
    "running_as_root": None,
    "claimed_at": None,
    "last_heartbeat_at": None,
    "active_until": None,
    "token_issued_at": None,
    "install_token_expires_at": None,
    "rotation_requested": False,
    "fleet_id": None,
    "type": "manual",
    "tags": [],
}


class TestHandleListAgentsAdmin:
    def _call(self, agents=None, tenant=_TENANT, token=ADMIN, tenant_id=TENANT_ID, tag=None):
        with patch("handlers.admin_agents._verify_admin", return_value=bool(tenant)) as va, \
             patch("handlers.admin_agents.tenants_repo") as tr, \
             patch("handlers.admin_agents.agents_repo") as ar:
            va.return_value = (token == ADMIN)
            tr.get.return_value = tenant
            ar.list_by_tenant.return_value = agents or [_AGENT_BASE]
            return handle_list_agents_admin(tenant_id, token, tag)

    def test_unauthorized_returns_401(self):
        r = handle_list_agents_admin(TENANT_ID, "bad-token")
        assert r["statusCode"] == 401

    def test_missing_tenant_id_returns_400(self):
        with patch("handlers.admin_agents._verify_admin", return_value=True):
            r = handle_list_agents_admin("", ADMIN)
        assert r["statusCode"] == 400

    def test_tenant_not_found_returns_404(self):
        with patch("handlers.admin_agents._verify_admin", return_value=True), \
             patch("handlers.admin_agents.tenants_repo") as tr:
            tr.get.return_value = None
            r = handle_list_agents_admin(TENANT_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_success_returns_agents(self):
        with patch("handlers.admin_agents._verify_admin", return_value=True), \
             patch("handlers.admin_agents.tenants_repo") as tr, \
             patch("handlers.admin_agents.agents_repo") as ar:
            tr.get.return_value = _TENANT
            ar.list_by_tenant.return_value = [_AGENT_BASE]
            r = handle_list_agents_admin(TENANT_ID, ADMIN)
        assert r["statusCode"] == 200
        agents = json.loads(r["body"])["agents"]
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "agent_1"

    def test_running_as_root_true_sets_access_level(self):
        agent = {**_AGENT_BASE, "running_as_root": "true", "mode": "wild"}
        with patch("handlers.admin_agents._verify_admin", return_value=True), \
             patch("handlers.admin_agents.tenants_repo") as tr, \
             patch("handlers.admin_agents.agents_repo") as ar:
            tr.get.return_value = _TENANT
            ar.list_by_tenant.return_value = [agent]
            r = handle_list_agents_admin(TENANT_ID, ADMIN)
        body = json.loads(r["body"])
        a = body["agents"][0]
        assert a["running_as_root"] == "true"
        assert a["access_level"] is not None

    def test_running_as_root_false_sets_access_level(self):
        agent = {**_AGENT_BASE, "running_as_root": "false", "mode": "readonly"}
        with patch("handlers.admin_agents._verify_admin", return_value=True), \
             patch("handlers.admin_agents.tenants_repo") as tr, \
             patch("handlers.admin_agents.agents_repo") as ar:
            tr.get.return_value = _TENANT
            ar.list_by_tenant.return_value = [agent]
            r = handle_list_agents_admin(TENANT_ID, ADMIN)
        body = json.loads(r["body"])
        a = body["agents"][0]
        assert a["running_as_root"] == "false"
        assert a["access_level"] is not None

    def test_running_as_root_none_access_level_is_none(self):
        agent = {**_AGENT_BASE, "running_as_root": None}
        with patch("handlers.admin_agents._verify_admin", return_value=True), \
             patch("handlers.admin_agents.tenants_repo") as tr, \
             patch("handlers.admin_agents.agents_repo") as ar:
            tr.get.return_value = _TENANT
            ar.list_by_tenant.return_value = [agent]
            r = handle_list_agents_admin(TENANT_ID, ADMIN)
        body = json.loads(r["body"])
        assert body["agents"][0]["access_level"] is None

    def test_tag_filter_applies(self):
        agent_with_tag = {**_AGENT_BASE, "agent_id": "agent_tagged", "tags": ["env:prod"]}
        agent_no_tag = {**_AGENT_BASE, "agent_id": "agent_plain", "tags": []}
        with patch("handlers.admin_agents._verify_admin", return_value=True), \
             patch("handlers.admin_agents.tenants_repo") as tr, \
             patch("handlers.admin_agents.agents_repo") as ar:
            tr.get.return_value = _TENANT
            ar.list_by_tenant.return_value = [agent_with_tag, agent_no_tag]
            r = handle_list_agents_admin(TENANT_ID, ADMIN, tag="env:prod")
        agents = json.loads(r["body"])["agents"]
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "agent_tagged"

    def test_empty_agent_list(self):
        with patch("handlers.admin_agents._verify_admin", return_value=True), \
             patch("handlers.admin_agents.tenants_repo") as tr, \
             patch("handlers.admin_agents.agents_repo") as ar:
            tr.get.return_value = _TENANT
            ar.list_by_tenant.return_value = []
            r = handle_list_agents_admin(TENANT_ID, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["agents"] == []


class TestListAgentsAdminHandler:
    def _evt(self, headers=None, qs=None):
        return {
            "headers": headers if headers is not None else {"authorization": f"Bearer {ADMIN}"},
            "queryStringParameters": qs or {"tenant_id": TENANT_ID},
        }

    def test_missing_auth_returns_401(self):
        r = list_agents_admin_handler(self._evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delegates_to_handler(self):
        with patch("handlers.admin_agents.handle_list_agents_admin", return_value={"statusCode": 200, "body": '{"agents":[]}'}) as h:
            list_agents_admin_handler(self._evt(qs={"tenant_id": TENANT_ID, "tag": "env:prod"}), None)
        h.assert_called_once_with(TENANT_ID, ADMIN, "env:prod")

    def test_no_tag_passes_none(self):
        with patch("handlers.admin_agents.handle_list_agents_admin", return_value={"statusCode": 200, "body": '{"agents":[]}'}) as h:
            list_agents_admin_handler(self._evt(qs={"tenant_id": TENANT_ID}), None)
        h.assert_called_once_with(TENANT_ID, ADMIN, None)
