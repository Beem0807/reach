import json
from unittest.mock import patch

from handlers.admin_agents import (
    handle_create_agent,
    handle_delete_agent,
    handle_reissue_install_token,
)

ADMIN = "test-admin-token"
TENANT = "tenant_1"
AGENT_ID = "agent_a"
API_URL = "https://api.example.com"

_TENANT = {"tenant_id": TENANT}
_AGENT_CREATED = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "CREATED"}
_AGENT_ACTIVE = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE"}
_AGENT_INACTIVE = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "INACTIVE"}


class TestCreateAgent:
    def _call(self, body=None, tenant_exists=True):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.tenants_repo") as tr:
            tr.get.return_value = _TENANT if tenant_exists else None
            return handle_create_agent(body or {"tenant_id": TENANT}, ADMIN, API_URL)

    def test_unauthorized(self):
        r = handle_create_agent({"tenant_id": TENANT}, "wrong", API_URL)
        assert r["statusCode"] == 401

    def test_tenant_id_required(self):
        with patch("handlers.admin_agents.agents_repo"), \
             patch("handlers.admin_agents.tenants_repo"):
            r = handle_create_agent({}, ADMIN, API_URL)
        assert r["statusCode"] == 400

    def test_tenant_not_found(self):
        r = self._call(tenant_exists=False)
        assert r["statusCode"] == 404

    def test_invalid_mode(self):
        r = self._call({"tenant_id": TENANT, "mode": "superuser"})
        assert r["statusCode"] == 400

    def test_creates_agent(self):
        r = self._call()
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert body["agent_id"].startswith("agent_")
        assert body["install_token"].startswith("install_")
        assert body["mode"] == "wild"
        assert "commands" in body

    def test_creates_agent_with_mode(self):
        r = self._call({"tenant_id": TENANT, "mode": "readonly"})
        assert r["statusCode"] == 201
        assert json.loads(r["body"])["mode"] == "readonly"

    def test_install_commands_included(self):
        r = self._call()
        commands = json.loads(r["body"])["commands"]
        assert "agent_linux" in commands
        assert "agent_mac_arm" in commands
        assert "cli_use" in commands

    def test_install_token_is_unique(self):
        with patch("handlers.admin_agents.agents_repo"), \
             patch("handlers.admin_agents.tenants_repo") as tr:
            tr.get.return_value = _TENANT
            r1 = handle_create_agent({"tenant_id": TENANT}, ADMIN, API_URL)
            r2 = handle_create_agent({"tenant_id": TENANT}, ADMIN, API_URL)
        t1 = json.loads(r1["body"])["install_token"]
        t2 = json.loads(r2["body"])["install_token"]
        assert t1 != t2


class TestDeleteAgent:
    def test_unauthorized(self):
        r = handle_delete_agent(AGENT_ID, {}, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo"):
            ar.get.return_value = None
            r = handle_delete_agent(AGENT_ID, {}, ADMIN)
        assert r["statusCode"] == 404

    def test_active_agent_blocked_without_force(self):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo"):
            ar.get.return_value = _AGENT_ACTIVE
            r = handle_delete_agent(AGENT_ID, {}, ADMIN)
        assert r["statusCode"] == 409

    def test_active_agent_deleted_with_force(self):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo") as ur:
            ar.get.return_value = _AGENT_ACTIVE
            r = handle_delete_agent(AGENT_ID, {"force": True}, ADMIN)
        assert r["statusCode"] == 200
        ar.delete.assert_called_once_with(AGENT_ID)

    def test_inactive_agent_deleted_without_force(self):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo"):
            ar.get.return_value = _AGENT_INACTIVE
            r = handle_delete_agent(AGENT_ID, {}, ADMIN)
        assert r["statusCode"] == 200

    def test_delete_cleans_up_user_access_lists(self):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo") as ur:
            ar.get.return_value = _AGENT_INACTIVE
            handle_delete_agent(AGENT_ID, {}, ADMIN)
        ur.remove_agent_from_all_users.assert_called_once_with(AGENT_ID, TENANT)


class TestReissueInstallToken:
    def test_unauthorized(self):
        r = handle_reissue_install_token(AGENT_ID, {}, "wrong", API_URL)
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_reissue_install_token(AGENT_ID, {}, ADMIN, API_URL)
        assert r["statusCode"] == 404

    def test_active_agent_blocked_without_force(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_ACTIVE
            r = handle_reissue_install_token(AGENT_ID, {}, ADMIN, API_URL)
        assert r["statusCode"] == 409

    def test_active_agent_reissued_with_force(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_ACTIVE
            r = handle_reissue_install_token(AGENT_ID, {"force": True}, ADMIN, API_URL)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["install_token"].startswith("install_")

    def test_created_agent_reissued_without_force(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_CREATED
            r = handle_reissue_install_token(AGENT_ID, {}, ADMIN, API_URL)
        assert r["statusCode"] == 200

    def test_new_install_token_is_unique(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_CREATED
            r1 = handle_reissue_install_token(AGENT_ID, {}, ADMIN, API_URL)
            r2 = handle_reissue_install_token(AGENT_ID, {}, ADMIN, API_URL)
        t1 = json.loads(r1["body"])["install_token"]
        t2 = json.loads(r2["body"])["install_token"]
        assert t1 != t2
