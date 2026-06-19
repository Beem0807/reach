import json
from unittest.mock import patch

from handlers.admin_agents import (
    handle_create_agent,
    handle_delete_agent,
    handle_reissue_install_token,
    handle_remove_agent,
    handle_revoke_agent,
)

ADMIN = "test-admin-token"
TENANT = "tenant_1"
AGENT_ID = "agent_a"
API_URL = "https://api.example.com"

_TENANT = {"tenant_id": TENANT}
_AGENT_CREATED = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "CREATED"}
_AGENT_ACTIVE = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE"}
_AGENT_INACTIVE = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "INACTIVE"}
_AGENT_REVOKED = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "REVOKED"}
_AGENT_DELETED = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "DELETED"}


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
        assert "agent" in commands
        assert "cli_use" in commands
        assert "install.sh" in commands["agent"]

    def test_default_command_has_yes_only(self):
        cmd = json.loads(self._call()["body"])["commands"]["agent"]
        assert "--yes" in cmd
        assert "--no-grant-service-mgmt" not in cmd
        assert "--grant-docker" not in cmd

    def test_service_mgmt_off_adds_no_grant_flag(self):
        cmd = json.loads(self._call({"tenant_id": TENANT, "grant_service_mgmt": False})["body"])["commands"]["agent"]
        assert "--yes" in cmd
        assert "--no-grant-service-mgmt" in cmd

    def test_grant_docker_opt_in(self):
        cmd = json.loads(self._call({"tenant_id": TENANT, "grant_docker": True})["body"])["commands"]["agent"]
        assert "--yes" in cmd
        assert "--grant-docker" in cmd

    def test_service_mgmt_off_and_docker_on(self):
        cmd = json.loads(self._call({"tenant_id": TENANT, "grant_service_mgmt": False, "grant_docker": True})["body"])["commands"]["agent"]
        assert "--yes" in cmd
        assert "--no-grant-service-mgmt" in cmd
        assert "--grant-docker" in cmd

    def test_install_token_is_unique(self):
        with patch("handlers.admin_agents.agents_repo"), \
             patch("handlers.admin_agents.tenants_repo") as tr:
            tr.get.return_value = _TENANT
            r1 = handle_create_agent({"tenant_id": TENANT}, ADMIN, API_URL)
            r2 = handle_create_agent({"tenant_id": TENANT}, ADMIN, API_URL)
        t1 = json.loads(r1["body"])["install_token"]
        t2 = json.loads(r2["body"])["install_token"]
        assert t1 != t2


class TestRevokeAgent:
    def _call(self, agent=_AGENT_ACTIVE):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo") as ur:
            ar.get.return_value = agent
            r = handle_revoke_agent(AGENT_ID, ADMIN)
            return r, ar, ur

    def test_unauthorized(self):
        r = handle_revoke_agent(AGENT_ID, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo"):
            ar.get.return_value = None
            r = handle_revoke_agent(AGENT_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_revokes_active_agent(self):
        r, ar, _ = self._call(_AGENT_ACTIVE)
        assert r["statusCode"] == 200
        import json
        assert json.loads(r["body"])["status"] == "REVOKED"
        ar.set_status.assert_called_once_with(AGENT_ID, "REVOKED")

    def test_revokes_inactive_agent(self):
        r, ar, _ = self._call(_AGENT_INACTIVE)
        assert r["statusCode"] == 200
        ar.set_status.assert_called_once_with(AGENT_ID, "REVOKED")

    def test_revokes_created_agent(self):
        r, ar, _ = self._call(_AGENT_CREATED)
        assert r["statusCode"] == 200
        ar.set_status.assert_called_once_with(AGENT_ID, "REVOKED")

    def test_already_revoked_returns_409(self):
        r, _, _ = self._call(_AGENT_REVOKED)
        assert r["statusCode"] == 409

    def test_already_deleted_returns_409(self):
        r, _, _ = self._call(_AGENT_DELETED)
        assert r["statusCode"] == 409

    def test_removes_from_user_access_lists(self):
        _, _, ur = self._call(_AGENT_ACTIVE)
        ur.remove_agent_from_all_users.assert_called_once_with(AGENT_ID, TENANT)


class TestDeleteAgent:
    def _call(self, agent=_AGENT_REVOKED):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo"):
            ar.get.return_value = agent
            r = handle_delete_agent(AGENT_ID, ADMIN)
            return r, ar

    def test_unauthorized(self):
        r = handle_delete_agent(AGENT_ID, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo"):
            ar.get.return_value = None
            r = handle_delete_agent(AGENT_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_active_agent_blocked(self):
        r, _ = self._call(_AGENT_ACTIVE)
        assert r["statusCode"] == 409

    def test_inactive_agent_blocked(self):
        r, _ = self._call(_AGENT_INACTIVE)
        assert r["statusCode"] == 409

    def test_created_agent_blocked(self):
        r, _ = self._call(_AGENT_CREATED)
        assert r["statusCode"] == 409

    def test_already_deleted_returns_409(self):
        r, _ = self._call(_AGENT_DELETED)
        assert r["statusCode"] == 409

    def test_revoked_agent_soft_deleted(self):
        r, ar = self._call(_AGENT_REVOKED)
        assert r["statusCode"] == 200
        import json
        assert json.loads(r["body"])["status"] == "DELETED"
        ar.set_status.assert_called_once_with(AGENT_ID, "DELETED")
        ar.delete.assert_not_called()


class TestRemoveAgent:
    def _call(self, agent=_AGENT_DELETED):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo"):
            ar.get.return_value = agent
            r = handle_remove_agent(AGENT_ID, ADMIN)
            return r, ar

    def test_unauthorized(self):
        r = handle_remove_agent(AGENT_ID, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_agents.agents_repo") as ar, \
             patch("handlers.admin_agents.users_repo"):
            ar.get.return_value = None
            r = handle_remove_agent(AGENT_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_active_agent_blocked(self):
        r, _ = self._call(_AGENT_ACTIVE)
        assert r["statusCode"] == 409

    def test_revoked_agent_blocked(self):
        r, _ = self._call(_AGENT_REVOKED)
        assert r["statusCode"] == 409

    def test_deleted_agent_physically_removed(self):
        r, ar = self._call(_AGENT_DELETED)
        assert r["statusCode"] == 200
        import json
        assert json.loads(r["body"])["removed"] is True
        ar.delete.assert_called_once_with(AGENT_ID)


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

    def test_revoked_agent_reissued_returns_created(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_REVOKED
            r = handle_reissue_install_token(AGENT_ID, {}, ADMIN, API_URL)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["install_token"].startswith("install_")
        ar.reissue_install_token.assert_called_once()

    def test_deleted_agent_blocked(self):
        with patch("handlers.admin_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_DELETED
            r = handle_reissue_install_token(AGENT_ID, {}, ADMIN, API_URL)
        assert r["statusCode"] == 409
