"""Tests for business-logic handlers in handlers/tenant_agents.py."""
import json
from unittest.mock import patch

from handlers.tenant_agents import (
    handle_acknowledge_capability,
    handle_create_tenant_agent,
    handle_get_agent_history,
    handle_reissue_tenant_install_token,
    handle_revoke_tenant_agent,
    handle_delete_tenant_agent,
    handle_remove_tenant_agent,
    handle_set_tenant_agent_mode,
    handle_request_agent_rotation,
    acknowledge_capability_handler,
    agent_history_handler,
    create_tenant_agent_handler,
    reissue_tenant_install_token_handler,
    revoke_tenant_agent_handler,
    delete_tenant_agent_handler,
    remove_tenant_agent_handler,
    set_tenant_agent_mode_handler,
    request_agent_rotation_handler,
)

TENANT_ID = "tenant_1"
AGENT_ID = "agent_abc"
TOKEN = "tok_test"
API_URL = "https://api.example.com"

_ADMIN = {"user_id": "user_admin", "tenant_id": TENANT_ID, "role": "admin", "username": "alice"}
_DEV = {"user_id": "user_dev", "tenant_id": TENANT_ID, "role": "developer", "username": "dev"}

_AGENT_CREATED = {"agent_id": AGENT_ID, "tenant_id": TENANT_ID, "status": "CREATED", "mode": "wild"}
_AGENT_ACTIVE = {**_AGENT_CREATED, "status": "ACTIVE"}
_AGENT_REVOKED = {**_AGENT_CREATED, "status": "REVOKED"}
_AGENT_DELETED = {**_AGENT_CREATED, "status": "DELETED"}


def _auth(user=_ADMIN):
    return patch("handlers.tenant_agents._verify_tenant_token", return_value=user)


# ---------------------------------------------------------------------------
# handle_create_tenant_agent
# ---------------------------------------------------------------------------

class TestHandleCreateTenantAgent:
    def _call(self, body=None, user=_ADMIN):
        with _auth(user), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.create.return_value = None
            r = handle_create_tenant_agent(body or {}, TOKEN, API_URL)
        return r, ar

    def test_unauthorized(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=None):
            r = handle_create_tenant_agent({}, TOKEN, API_URL)
        assert r["statusCode"] == 401

    def test_developer_role_forbidden(self):
        r, _ = self._call(user=_DEV)
        assert r["statusCode"] == 403

    def test_invalid_mode_returns_400(self):
        r, _ = self._call(body={"mode": "supermode"})
        assert r["statusCode"] == 400

    def test_success_returns_201_with_install_commands(self):
        r, _ = self._call(body={"mode": "wild"})
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert "agent_id" in body
        assert body["agent_id"].startswith("agent_")
        assert "install_token" in body
        assert "commands" in body
        assert "agent" in body["commands"]
        assert "cli_use" in body["commands"]

    def test_mode_stored_in_response(self):
        r, _ = self._call(body={"mode": "readonly"})
        assert json.loads(r["body"])["mode"] == "readonly"

    def test_default_type_is_host_with_curl_command(self):
        r, ar = self._call(body={"mode": "wild"})
        body = json.loads(r["body"])
        assert body["type"] == "host"
        assert "agent" in body["commands"]  # curl installer
        assert "helm" not in body["commands"]
        assert ar.create.call_args[0][0]["type"] == "host"

    def test_k8s_type_returns_helm_command(self):
        r, ar = self._call(body={"mode": "wild", "type": "k8s"})
        body = json.loads(r["body"])
        assert body["type"] == "k8s"
        assert "helm" in body["commands"]
        assert "agent" not in body["commands"]
        helm = body["commands"]["helm"]
        # Installs from the published Helm repo (not a local path). The image is
        # not pinned separately - it resolves from the chart's appVersion, so the
        # command carries no --set image.tag.
        assert "helm repo add reach" in helm
        assert "helm install reach-agent reach/reach-agent" in helm
        assert "deploy/helm/reach-agent" not in helm
        assert "--set image.tag=" not in helm
        assert "reach.installToken" in helm
        assert ar.create.call_args[0][0]["type"] == "k8s"

    def test_k8s_ignores_host_grants(self):
        # Docker / service-mgmt are host-only; a k8s agent must not carry them.
        r, ar = self._call(body={"mode": "wild", "type": "k8s",
                                 "grant_docker": True, "grant_service_mgmt": True})
        created = ar.create.call_args[0][0]
        assert created["grant_docker"] is False
        assert created["grant_service_mgmt"] is False

    def test_invalid_type_returns_400(self):
        r, _ = self._call(body={"mode": "wild", "type": "vm"})
        assert r["statusCode"] == 400

    def test_approved_mode_accepted(self):
        r, _ = self._call(body={"mode": "approved"})
        assert r["statusCode"] == 201

    def test_agent_created_in_repo(self):
        r, ar = self._call(body={"mode": "wild"})
        ar.create.assert_called_once()
        call_arg = ar.create.call_args[0][0]
        assert call_arg["tenant_id"] == TENANT_ID
        assert call_arg["status"] == "CREATED"

    def test_install_token_prefix(self):
        r, _ = self._call()
        token = json.loads(r["body"])["install_token"]
        assert token.startswith("install_")


# ---------------------------------------------------------------------------
# handle_reissue_tenant_install_token
# ---------------------------------------------------------------------------

class TestHandleReissueTenantInstallToken:
    def _call(self, agent=_AGENT_CREATED, body=None, user=_ADMIN):
        with _auth(user), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo") as hr:
            ar.get.return_value = agent
            ar.reissue_install_token.return_value = None
            hr.create.return_value = None
            r = handle_reissue_tenant_install_token(AGENT_ID, body or {}, TOKEN, API_URL)
        return r, ar

    def test_unauthorized(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=None):
            r = handle_reissue_tenant_install_token(AGENT_ID, {}, TOKEN, API_URL)
        assert r["statusCode"] == 401

    def test_developer_forbidden(self):
        r, _ = self._call(user=_DEV)
        assert r["statusCode"] == 403

    def test_agent_not_found_returns_404(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_reissue_tenant_install_token(AGENT_ID, {}, TOKEN, API_URL)
        assert r["statusCode"] == 404

    def test_deleted_agent_returns_409(self):
        r, _ = self._call(agent=_AGENT_DELETED)
        assert r["statusCode"] == 409

    def test_active_agent_without_force_returns_409(self):
        r, _ = self._call(agent=_AGENT_ACTIVE, body={})
        assert r["statusCode"] == 409

    def test_active_agent_with_force_succeeds(self):
        r, _ = self._call(agent=_AGENT_ACTIVE, body={"force": True})
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert "install_token" in body
        assert "commands" in body

    def test_created_agent_succeeds(self):
        r, ar = self._call(agent=_AGENT_CREATED)
        assert r["statusCode"] == 200
        ar.reissue_install_token.assert_called_once()

    def test_history_record_created(self):
        with _auth(), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo") as hr:
            ar.get.return_value = _AGENT_CREATED
            ar.reissue_install_token.return_value = None
            hr.create.return_value = None
            handle_reissue_tenant_install_token(AGENT_ID, {}, TOKEN, API_URL)
        hr.create.assert_called_once()
        call_arg = hr.create.call_args[0][0]
        assert call_arg["to_status"] == "CREATED"
        assert call_arg["note"] == "install token reissued"


# ---------------------------------------------------------------------------
# handle_revoke_tenant_agent
# ---------------------------------------------------------------------------

class TestHandleRevokeTenantAgent:
    def _call(self, agent=_AGENT_ACTIVE, user=_ADMIN):
        with _auth(user), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.users_repo") as ur, \
             patch("handlers.tenant_agents.agent_history_repo") as hr:
            ar.get.return_value = agent
            ar.set_status.return_value = None
            ur.remove_agent_from_all_users.return_value = None
            hr.create.return_value = None
            r = handle_revoke_tenant_agent(AGENT_ID, TOKEN)
        return r, ar

    def test_unauthorized(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=None):
            r = handle_revoke_tenant_agent(AGENT_ID, TOKEN)
        assert r["statusCode"] == 401

    def test_developer_forbidden(self):
        r, _ = self._call(user=_DEV)
        assert r["statusCode"] == 403

    def test_agent_not_found_returns_404(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_revoke_tenant_agent(AGENT_ID, TOKEN)
        assert r["statusCode"] == 404

    def test_already_revoked_returns_409(self):
        r, _ = self._call(agent=_AGENT_REVOKED)
        assert r["statusCode"] == 409

    def test_already_deleted_returns_409(self):
        r, _ = self._call(agent=_AGENT_DELETED)
        assert r["statusCode"] == 409

    def test_success_sets_revoked_status(self):
        r, ar = self._call()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["status"] == "REVOKED"
        ar.set_status.assert_called_once_with(AGENT_ID, "REVOKED")

    def test_history_record_created(self):
        with _auth(), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.users_repo") as ur, \
             patch("handlers.tenant_agents.agent_history_repo") as hr:
            ar.get.return_value = _AGENT_ACTIVE
            ar.set_status.return_value = None
            ur.remove_agent_from_all_users.return_value = None
            hr.create.return_value = None
            handle_revoke_tenant_agent(AGENT_ID, TOKEN)
        hr.create.assert_called_once()
        call_arg = hr.create.call_args[0][0]
        assert call_arg["to_status"] == "REVOKED"


# ---------------------------------------------------------------------------
# handle_delete_tenant_agent
# ---------------------------------------------------------------------------

class TestHandleDeleteTenantAgent:
    def _call(self, agent=_AGENT_REVOKED, user=_ADMIN):
        with _auth(user), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo") as hr:
            ar.get.return_value = agent
            ar.set_status.return_value = None
            hr.create.return_value = None
            r = handle_delete_tenant_agent(AGENT_ID, TOKEN)
        return r, ar

    def test_unauthorized(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=None):
            r = handle_delete_tenant_agent(AGENT_ID, TOKEN)
        assert r["statusCode"] == 401

    def test_developer_forbidden(self):
        r, _ = self._call(user=_DEV)
        assert r["statusCode"] == 403

    def test_agent_not_found_returns_404(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_delete_tenant_agent(AGENT_ID, TOKEN)
        assert r["statusCode"] == 404

    def test_already_deleted_returns_409(self):
        r, _ = self._call(agent=_AGENT_DELETED)
        assert r["statusCode"] == 409

    def test_not_revoked_first_returns_409_with_current_status(self):
        r, _ = self._call(agent=_AGENT_ACTIVE)
        assert r["statusCode"] == 409
        assert "ACTIVE" in json.loads(r["body"])["error"]

    def test_success_sets_deleted_status(self):
        r, ar = self._call()
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["status"] == "DELETED"
        ar.set_status.assert_called_once_with(AGENT_ID, "DELETED")

    def test_history_record_created(self):
        with _auth(), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo") as hr:
            ar.get.return_value = _AGENT_REVOKED
            ar.set_status.return_value = None
            hr.create.return_value = None
            handle_delete_tenant_agent(AGENT_ID, TOKEN)
        hr.create.assert_called_once()
        call_arg = hr.create.call_args[0][0]
        assert call_arg["to_status"] == "DELETED"


# ---------------------------------------------------------------------------
# handle_remove_tenant_agent
# ---------------------------------------------------------------------------

class TestHandleRemoveTenantAgent:
    def _call(self, agent=_AGENT_DELETED, user=_ADMIN):
        with _auth(user), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = agent
            ar.delete.return_value = None
            r = handle_remove_tenant_agent(AGENT_ID, TOKEN)
        return r, ar

    def test_unauthorized(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=None):
            r = handle_remove_tenant_agent(AGENT_ID, TOKEN)
        assert r["statusCode"] == 401

    def test_developer_forbidden(self):
        r, _ = self._call(user=_DEV)
        assert r["statusCode"] == 403

    def test_agent_not_found_returns_404(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_remove_tenant_agent(AGENT_ID, TOKEN)
        assert r["statusCode"] == 404

    def test_not_deleted_first_returns_409(self):
        r, _ = self._call(agent=_AGENT_REVOKED)
        assert r["statusCode"] == 409
        assert "REVOKED" in json.loads(r["body"])["error"]

    def test_success_permanently_removes_agent(self):
        r, ar = self._call()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["removed"] is True
        ar.delete.assert_called_once_with(AGENT_ID)


# ---------------------------------------------------------------------------
# Lambda handler wrappers (missing auth = 401)
# ---------------------------------------------------------------------------

_OK = {"statusCode": 200, "headers": {}, "body": "{}"}
_BEARER = {"authorization": "Bearer tok"}


def _evt(headers=None, body=None, path=None):
    return {
        "headers": _BEARER if headers is None else headers,
        "body": body,
        "pathParameters": path or {"agent_id": AGENT_ID},
        "queryStringParameters": {},
    }


# ---------------------------------------------------------------------------
# handle_set_tenant_agent_mode
# ---------------------------------------------------------------------------

class TestHandleSetTenantAgentMode:
    def _call(self, mode="wild", agent=_AGENT_ACTIVE, user=_ADMIN):
        with _auth(user), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = agent
            ar.update_policy.return_value = None
            r = handle_set_tenant_agent_mode(AGENT_ID, {"mode": mode}, TOKEN)
        return r, ar

    def test_unauthorized(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=None):
            r = handle_set_tenant_agent_mode(AGENT_ID, {"mode": "wild"}, TOKEN)
        assert r["statusCode"] == 401

    def test_developer_forbidden(self):
        r, _ = self._call(user=_DEV)
        assert r["statusCode"] == 403

    def test_invalid_mode_returns_400(self):
        r, _ = self._call(mode="invalid")
        assert r["statusCode"] == 400

    def test_agent_not_found_returns_404(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_set_tenant_agent_mode(AGENT_ID, {"mode": "wild"}, TOKEN)
        assert r["statusCode"] == 404

    def test_success_sets_mode(self):
        r, ar = self._call(mode="readonly")
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["mode"] == "readonly"
        ar.update_policy.assert_called_once_with(AGENT_ID, "readonly")

    def test_all_valid_modes_accepted(self):
        for mode in ("wild", "readonly", "approved"):
            r, _ = self._call(mode=mode)
            assert r["statusCode"] == 200


# ---------------------------------------------------------------------------
# Also test grant_service_mgmt=False and grant_docker=True in install commands
# ---------------------------------------------------------------------------

class TestInstallCommandFlags:
    def _create(self, body):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.create.return_value = None
            r = handle_create_tenant_agent(body, TOKEN, API_URL)
        return json.loads(r["body"])["commands"]["agent"], ar

    def test_no_grant_service_mgmt_flag_in_command(self):
        cmd, _ = self._create({"grant_service_mgmt": False})
        assert "--no-grant-service-mgmt" in cmd

    def test_grant_docker_flag_in_command(self):
        cmd, _ = self._create({"grant_docker": True})
        assert "--grant-docker" in cmd

    # --force ensures config is overwritten on reinstall (prevents stale agent_token bug)
    def test_force_flag_always_present(self):
        cmd, _ = self._create({})
        assert "--force" in cmd

    def test_force_flag_present_with_grants(self):
        cmd, _ = self._create({"grant_docker": True, "grant_service_mgmt": True})
        assert "--force" in cmd

    # install.sh always requires root (writes to /etc/reach-agent, /usr/local/bin)
    def test_sudo_always_present(self):
        cmd, _ = self._create({"grant_docker": False, "grant_service_mgmt": False})
        assert "sudo" in cmd

    def test_sudo_when_grant_docker(self):
        cmd, _ = self._create({"grant_docker": True})
        assert "sudo" in cmd

    def test_sudo_when_grant_service_mgmt(self):
        cmd, _ = self._create({"grant_service_mgmt": True})
        assert "sudo" in cmd

    def test_sudo_when_both_grants(self):
        cmd, _ = self._create({"grant_docker": True, "grant_service_mgmt": True})
        assert "sudo" in cmd

    # grant flags stored in repo on create
    def test_grant_flags_stored_on_create(self):
        _, ar = self._create({"grant_docker": True, "grant_service_mgmt": True})
        stored = ar.create.call_args[0][0]
        assert stored["grant_docker"] is True
        assert stored["grant_service_mgmt"] is True

    def test_grant_flags_default_false_on_create(self):
        _, ar = self._create({})
        stored = ar.create.call_args[0][0]
        assert stored["grant_docker"] is False
        assert stored["grant_service_mgmt"] is False

    # grant flags passed to repo on reissue
    def test_grant_flags_passed_to_reissue_repo(self):
        with _auth(), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo"):
            ar.get.return_value = _AGENT_CREATED
            ar.reissue_install_token.return_value = None
            handle_reissue_tenant_install_token(
                AGENT_ID, {"grant_docker": True, "grant_service_mgmt": True}, TOKEN, API_URL,
            )
        _, kwargs = ar.reissue_install_token.call_args
        assert kwargs["grant_docker"] is True
        assert kwargs["grant_service_mgmt"] is True

    def test_reissue_sudo_when_grant_docker(self):
        with _auth(), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo"):
            ar.get.return_value = _AGENT_CREATED
            ar.reissue_install_token.return_value = None
            r = handle_reissue_tenant_install_token(
                AGENT_ID, {"grant_docker": True}, TOKEN, API_URL,
            )
        cmd = json.loads(r["body"])["commands"]["agent"]
        assert "sudo" in cmd

    def test_reissue_sudo_always_present(self):
        with _auth(), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo"):
            ar.get.return_value = _AGENT_CREATED
            ar.reissue_install_token.return_value = None
            r = handle_reissue_tenant_install_token(AGENT_ID, {}, TOKEN, API_URL)
        cmd = json.loads(r["body"])["commands"]["agent"]
        assert "sudo" in cmd

    def test_reissue_force_flag_present(self):
        # --force overwrites existing config so the new install_token is written,
        # preventing the agent from restarting with a stale (invalidated) agent_token.
        with _auth(), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo"):
            ar.get.return_value = _AGENT_CREATED
            ar.reissue_install_token.return_value = None
            r = handle_reissue_tenant_install_token(AGENT_ID, {}, TOKEN, API_URL)
        cmd = json.loads(r["body"])["commands"]["agent"]
        assert "--force" in cmd


class TestLambdaHandlersMissingAuth:
    def test_create_missing_auth(self):
        r = create_tenant_agent_handler(_evt(headers={}, body="{}"), None)
        assert r["statusCode"] == 401

    def test_reissue_missing_auth(self):
        r = reissue_tenant_install_token_handler(_evt(headers={}, body="{}"), None)
        assert r["statusCode"] == 401

    def test_revoke_missing_auth(self):
        r = revoke_tenant_agent_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delete_missing_auth(self):
        r = delete_tenant_agent_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_remove_missing_auth(self):
        r = remove_tenant_agent_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_set_mode_missing_auth(self):
        r = set_tenant_agent_mode_handler(_evt(headers={}, body="{}"), None)
        assert r["statusCode"] == 401


class TestLambdaHandlersDelegation:
    def test_create_delegates(self):
        with patch("handlers.tenant_agents.handle_create_tenant_agent", return_value=_OK) as h:
            create_tenant_agent_handler(_evt(body='{"mode":"wild"}'), None)
        h.assert_called_once()

    def test_reissue_delegates(self):
        with patch("handlers.tenant_agents.handle_reissue_tenant_install_token", return_value=_OK) as h:
            reissue_tenant_install_token_handler(_evt(body='{}'), None)
        h.assert_called_once()
        assert h.call_args[0][0] == AGENT_ID

    def test_revoke_delegates(self):
        with patch("handlers.tenant_agents.handle_revoke_tenant_agent", return_value=_OK) as h:
            revoke_tenant_agent_handler(_evt(), None)
        h.assert_called_once_with(AGENT_ID, "tok")

    def test_delete_delegates(self):
        with patch("handlers.tenant_agents.handle_delete_tenant_agent", return_value=_OK) as h:
            delete_tenant_agent_handler(_evt(), None)
        h.assert_called_once_with(AGENT_ID, "tok")

    def test_remove_delegates(self):
        with patch("handlers.tenant_agents.handle_remove_tenant_agent", return_value=_OK) as h:
            remove_tenant_agent_handler(_evt(), None)
        h.assert_called_once_with(AGENT_ID, "tok")


# ---------------------------------------------------------------------------
# handle_request_agent_rotation
# ---------------------------------------------------------------------------

class TestRequestAgentRotation:
    def test_unauthorized(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=None):
            r = handle_request_agent_rotation(AGENT_ID, TOKEN)
        assert r["statusCode"] == 401

    def test_forbidden_non_admin(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=_DEV), \
             patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_ACTIVE
            r = handle_request_agent_rotation(AGENT_ID, TOKEN)
        assert r["statusCode"] == 403

    def test_agent_not_found(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=_ADMIN), \
             patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_request_agent_rotation(AGENT_ID, TOKEN)
        assert r["statusCode"] == 404

    def test_agent_wrong_tenant_returns_404(self):
        other_tenant_agent = {**_AGENT_ACTIVE, "tenant_id": "other_tenant"}
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=_ADMIN), \
             patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = other_tenant_agent
            r = handle_request_agent_rotation(AGENT_ID, TOKEN)
        assert r["statusCode"] == 404

    def test_agent_not_active_returns_409(self):
        for status in ("CREATED", "REVOKED", "DELETED"):
            agent = {**_AGENT_CREATED, "status": status}
            with patch("handlers.tenant_agents._verify_tenant_token", return_value=_ADMIN), \
                 patch("handlers.tenant_agents.agents_repo") as ar:
                ar.get.return_value = agent
                r = handle_request_agent_rotation(AGENT_ID, TOKEN)
            assert r["statusCode"] == 409, f"expected 409 for status={status}"

    def test_success_calls_request_rotation(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=_ADMIN), \
             patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = _AGENT_ACTIVE
            r = handle_request_agent_rotation(AGENT_ID, TOKEN)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["agent_id"] == AGENT_ID
        assert body["rotation_requested"] is True
        ar.request_rotation.assert_called_once_with(AGENT_ID)


# ---------------------------------------------------------------------------
# request_agent_rotation_handler (Lambda wrapper)
# ---------------------------------------------------------------------------

class TestRequestAgentRotationHandler:
    def test_missing_auth(self):
        r = request_agent_rotation_handler({"headers": {}, "pathParameters": {"agent_id": AGENT_ID}}, None)
        assert r["statusCode"] == 401

    def test_delegates_to_handler(self):
        with patch("handlers.tenant_agents.handle_request_agent_rotation", return_value=_OK) as h:
            request_agent_rotation_handler(_evt(), None)
        h.assert_called_once_with(AGENT_ID, "tok")


# ---------------------------------------------------------------------------
# handle_acknowledge_capability
# ---------------------------------------------------------------------------

_AGENT_WITH_DOCKER = {**_AGENT_ACTIVE, "grant_docker": False, "grant_service_mgmt": False, "hostname": "myhost"}


class TestHandleAcknowledgeCapability:
    def _call(self, capability, agent=_AGENT_WITH_DOCKER, user=_ADMIN):
        with _auth(user), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.audit") as mock_audit:
            ar.get.return_value = agent
            ar.update_grants.return_value = None
            r = handle_acknowledge_capability(AGENT_ID, {"capability": capability}, TOKEN)
        return r, ar, mock_audit

    def test_unauthorized(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=None):
            r = handle_acknowledge_capability(AGENT_ID, {"capability": "docker"}, TOKEN)
        assert r["statusCode"] == 401

    def test_developer_forbidden(self):
        r, _, _ = self._call("docker", user=_DEV)
        assert r["statusCode"] == 403

    def test_agent_not_found_returns_404(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.audit"):
            ar.get.return_value = None
            r = handle_acknowledge_capability(AGENT_ID, {"capability": "docker"}, TOKEN)
        assert r["statusCode"] == 404

    def test_invalid_capability_returns_400(self):
        r, _, _ = self._call("invalid_cap")
        assert r["statusCode"] == 400
        assert "capability" in json.loads(r["body"])["error"]

    def test_empty_capability_returns_400(self):
        r, _, _ = self._call("")
        assert r["statusCode"] == 400

    def test_docker_acknowledge_updates_grant_docker(self):
        _, ar, _ = self._call("docker")
        ar.update_grants.assert_called_once_with(AGENT_ID, grant_docker=True)

    def test_service_mgmt_acknowledge_updates_grant_service_mgmt(self):
        _, ar, _ = self._call("service_mgmt")
        ar.update_grants.assert_called_once_with(AGENT_ID, grant_service_mgmt=True)

    def test_docker_does_not_touch_service_mgmt_grant(self):
        _, ar, _ = self._call("docker")
        _, kwargs = ar.update_grants.call_args
        assert "grant_service_mgmt" not in kwargs

    def test_service_mgmt_does_not_touch_docker_grant(self):
        _, ar, _ = self._call("service_mgmt")
        _, kwargs = ar.update_grants.call_args
        assert "grant_docker" not in kwargs

    def test_acknowledge_writes_audit_event(self):
        _, _, mock_audit = self._call("docker")
        mock_audit.write.assert_called_once()
        call_kwargs = mock_audit.write.call_args
        assert call_kwargs[0][0] == "agent.capability_acknowledged"

    def test_audit_metadata_contains_capability(self):
        _, _, mock_audit = self._call("docker")
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["capability"] == "docker"

    def test_audit_metadata_service_mgmt(self):
        _, _, mock_audit = self._call("service_mgmt")
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["capability"] == "service_mgmt"

    def test_audit_metadata_contains_hostname(self):
        _, _, mock_audit = self._call("docker")
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["hostname"] == "myhost"

    def test_audit_resource_id_is_agent_id(self):
        _, _, mock_audit = self._call("docker")
        assert mock_audit.write.call_args[1]["resource_id"] == AGENT_ID

    def test_success_returns_acknowledged_true(self):
        r, _, _ = self._call("docker")
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["acknowledged"] is True
        assert body["agent_id"] == AGENT_ID
        assert body["capability"] == "docker"


# ---------------------------------------------------------------------------
# handle_set_tenant_agent_mode - audit behaviour
# ---------------------------------------------------------------------------

class TestSetTenantAgentModeAudit:
    def _call(self, new_mode, current_mode="wild", user=_ADMIN):
        agent = {**_AGENT_ACTIVE, "mode": current_mode, "hostname": "myhost"}
        with _auth(user), \
             patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.audit") as mock_audit:
            ar.get.return_value = agent
            ar.update_policy.return_value = None
            r = handle_set_tenant_agent_mode(AGENT_ID, {"mode": new_mode}, TOKEN)
        return r, mock_audit

    def test_mode_change_writes_audit(self):
        _, mock_audit = self._call(new_mode="approved", current_mode="wild")
        mock_audit.write.assert_called_once()
        assert mock_audit.write.call_args[0][0] == "agent.mode_changed"

    def test_audit_metadata_contains_from_and_to_mode(self):
        _, mock_audit = self._call(new_mode="approved", current_mode="wild")
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["from_mode"] == "wild"
        assert meta["to_mode"] == "approved"

    def test_audit_resource_id_is_agent_id(self):
        _, mock_audit = self._call(new_mode="readonly", current_mode="wild")
        assert mock_audit.write.call_args[1]["resource_id"] == AGENT_ID

    def test_same_mode_no_audit_written(self):
        _, mock_audit = self._call(new_mode="wild", current_mode="wild")
        mock_audit.write.assert_not_called()

    def test_audit_metadata_contains_hostname(self):
        _, mock_audit = self._call(new_mode="approved", current_mode="wild")
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["hostname"] == "myhost"

    def test_all_mode_transitions_write_audit(self):
        pairs = [("wild", "readonly"), ("readonly", "approved"), ("approved", "wild")]
        for from_m, to_m in pairs:
            _, mock_audit = self._call(new_mode=to_m, current_mode=from_m)
            mock_audit.write.assert_called_once(), f"no audit for {from_m}→{to_m}"


# ---------------------------------------------------------------------------
# acknowledge_capability_handler (Lambda wrapper)
# ---------------------------------------------------------------------------

class TestAcknowledgeCapabilityHandler:
    def test_missing_auth_returns_401(self):
        r = acknowledge_capability_handler(_evt(headers={}, body='{"capability":"docker"}'), None)
        assert r["statusCode"] == 401

    def test_invalid_json_returns_400(self):
        r = acknowledge_capability_handler(_evt(body="not-json"), None)
        assert r["statusCode"] == 400

    def test_delegates_to_handler(self):
        with patch("handlers.tenant_agents.handle_acknowledge_capability", return_value=_OK) as h:
            acknowledge_capability_handler(_evt(body='{"capability":"docker"}'), None)
        h.assert_called_once()
        assert h.call_args[0][0] == AGENT_ID
        assert h.call_args[0][2] == "tok"

    def test_delegates_capability_in_body(self):
        with patch("handlers.tenant_agents.handle_acknowledge_capability", return_value=_OK) as h:
            acknowledge_capability_handler(_evt(body='{"capability":"service_mgmt"}'), None)
        body_arg = h.call_args[0][1]
        assert body_arg["capability"] == "service_mgmt"

    def test_missing_auth_in_lambda_wrappers(self):
        r = acknowledge_capability_handler({"headers": {}, "pathParameters": {"agent_id": AGENT_ID}}, None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# handle_get_agent_history
# ---------------------------------------------------------------------------

class TestHandleGetAgentHistory:
    def test_unauthorized(self):
        with patch("handlers.tenant_agents._verify_tenant_token", return_value=None):
            r = handle_get_agent_history(AGENT_ID, TOKEN)
        assert r["statusCode"] == 401

    def test_agent_not_found_returns_404(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_get_agent_history(AGENT_ID, TOKEN)
        assert r["statusCode"] == 404

    def test_agent_in_other_tenant_returns_404(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar:
            ar.get.return_value = {**_AGENT_ACTIVE, "tenant_id": "other_tenant"}
            r = handle_get_agent_history(AGENT_ID, TOKEN)
        assert r["statusCode"] == 404

    def test_success_returns_history(self):
        records = [
            {"from_status": "ACTIVE", "to_status": "INACTIVE", "triggered_by": "heartbeat"},
            {"from_status": "INACTIVE", "to_status": "ACTIVE", "triggered_by": "sync"},
        ]
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo") as hr:
            ar.get.return_value = _AGENT_ACTIVE
            hr.list_by_agent.return_value = records
            r = handle_get_agent_history(AGENT_ID, TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["history"] == records
        hr.list_by_agent.assert_called_once_with(AGENT_ID, limit=50)

    def test_empty_history_returns_empty_list(self):
        with _auth(), patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo") as hr:
            ar.get.return_value = _AGENT_ACTIVE
            hr.list_by_agent.return_value = []
            r = handle_get_agent_history(AGENT_ID, TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["history"] == []

    def test_developer_role_can_view_history(self):
        # History is not admin-gated; any tenant member who can see the agent can read it.
        with _auth(_DEV), patch("handlers.tenant_agents.agents_repo") as ar, \
             patch("handlers.tenant_agents.agent_history_repo") as hr:
            ar.get.return_value = _AGENT_ACTIVE
            hr.list_by_agent.return_value = []
            r = handle_get_agent_history(AGENT_ID, TOKEN)
        assert r["statusCode"] == 200


# ---------------------------------------------------------------------------
# agent_history_handler (Lambda wrapper)
# ---------------------------------------------------------------------------

class TestAgentHistoryHandler:
    def test_missing_auth_returns_401(self):
        r = agent_history_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delegates_to_handler(self):
        with patch("handlers.tenant_agents.handle_get_agent_history", return_value=_OK) as h:
            r = agent_history_handler(_evt(), None)
        h.assert_called_once()
        assert h.call_args[0][0] == AGENT_ID
        assert h.call_args[0][1] == "tok"
        assert r == _OK

    def test_agent_id_read_from_path(self):
        with patch("handlers.tenant_agents.handle_get_agent_history", return_value=_OK) as h:
            agent_history_handler(_evt(path={"agent_id": "agent_xyz"}), None)
        assert h.call_args[0][0] == "agent_xyz"
