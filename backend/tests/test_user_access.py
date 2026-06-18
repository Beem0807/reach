import json
import pytest
from unittest.mock import patch, MagicMock

from handlers.admin_users import (
    handle_get_user_agents,
    handle_set_user_agents,
    handle_grant_agent_access,
    handle_revoke_agent_access,
)

ADMIN = "test-admin-token"
TENANT = "tenant_1"
USER_ID = "user_1"

_USER_UNRESTRICTED = {"user_id": USER_ID, "tenant_id": TENANT}  # no allowed_agent_ids key
_USER_WILDCARD = {"user_id": USER_ID, "tenant_id": TENANT, "allowed_agent_ids": ["*"]}
_USER_RESTRICTED = {"user_id": USER_ID, "tenant_id": TENANT, "allowed_agent_ids": ["agent_a"]}
_USER_LOCKED = {"user_id": USER_ID, "tenant_id": TENANT, "allowed_agent_ids": []}

_AGENT_A = {"agent_id": "agent_a", "tenant_id": TENANT}
_AGENT_B = {"agent_id": "agent_b", "tenant_id": TENANT}


# ---------------------------------------------------------------------------
# handle_get_user_agents
# ---------------------------------------------------------------------------

class TestGetUserAgents:
    def _call(self, user_record=_USER_UNRESTRICTED):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = user_record
            return handle_get_user_agents(TENANT, USER_ID, ADMIN)

    def test_unauthorized(self):
        r = handle_get_user_agents(TENANT, USER_ID, "wrong")
        assert r["statusCode"] == 401

    def test_user_not_found(self):
        with patch("handlers.admin_users.users_repo") as ur:
            ur.get.return_value = None
            r = handle_get_user_agents(TENANT, USER_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_null_allowed_agent_ids_returns_wildcard(self):
        r = self._call(_USER_UNRESTRICTED)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["allowed_agent_ids"] == ["*"]

    def test_explicit_wildcard_returned(self):
        r = self._call(_USER_WILDCARD)
        assert json.loads(r["body"])["allowed_agent_ids"] == ["*"]

    def test_restricted_list_returned(self):
        r = self._call(_USER_RESTRICTED)
        assert json.loads(r["body"])["allowed_agent_ids"] == ["agent_a"]

    def test_empty_list_returned(self):
        r = self._call(_USER_LOCKED)
        assert json.loads(r["body"])["allowed_agent_ids"] == []


# ---------------------------------------------------------------------------
# handle_set_user_agents
# ---------------------------------------------------------------------------

class TestSetUserAgents:
    def _call(self, body, user_record=_USER_RESTRICTED, agents=None):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.agents_repo") as ar, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = user_record
            ar.list_by_tenant.return_value = agents or [_AGENT_A, _AGENT_B]
            return handle_set_user_agents(TENANT, USER_ID, body, ADMIN)

    def test_unauthorized(self):
        r = handle_set_user_agents(TENANT, USER_ID, {}, "wrong")
        assert r["statusCode"] == 401

    def test_user_not_found(self):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.agents_repo"), \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = None
            r = handle_set_user_agents(TENANT, USER_ID, {"agent_ids": ["agent_a"]}, ADMIN)
        assert r["statusCode"] == 404

    def test_non_list_body_rejected(self):
        r = self._call({"agent_ids": "agent_a"})
        assert r["statusCode"] == 400

    def test_missing_agent_ids_rejected(self):
        r = self._call({})
        assert r["statusCode"] == 400

    def test_wildcard_unrestricts_user(self):
        r = self._call({"agent_ids": ["*"]})
        assert r["statusCode"] == 200
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.set_allowed_agents.assert_called  # called with ["*"]

    def test_empty_list_locks_out(self):
        r = self._call({"agent_ids": []})
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["allowed_agent_ids"] == []

    def test_valid_specific_list_stored(self):
        r = self._call({"agent_ids": ["agent_a"]})
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["allowed_agent_ids"] == ["agent_a"]

    def test_unknown_agent_rejected(self):
        r = self._call({"agent_ids": ["agent_unknown"]})
        assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# handle_grant_agent_access
# ---------------------------------------------------------------------------

class TestGrantAgentAccess:
    def _call(self, user_record=_USER_RESTRICTED, agent_record=_AGENT_B):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.agents_repo") as ar, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = user_record
            ar.get.return_value = agent_record
            return handle_grant_agent_access(TENANT, USER_ID, agent_record["agent_id"], ADMIN)

    def test_unauthorized(self):
        r = handle_grant_agent_access(TENANT, USER_ID, "agent_b", "wrong")
        assert r["statusCode"] == 401

    def test_user_not_found(self):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = None
            r = handle_grant_agent_access(TENANT, USER_ID, "agent_b", ADMIN)
        assert r["statusCode"] == 404

    def test_wildcard_user_returns_409(self):
        r = self._call(user_record=_USER_WILDCARD)
        assert r["statusCode"] == 409

    def test_unrestricted_null_user_returns_409(self):
        # null allowed_agent_ids → displayed as ["*"] → must 409
        r = self._call(user_record=_USER_UNRESTRICTED)
        assert r["statusCode"] == 409

    def test_agent_not_in_tenant_returns_404(self):
        wrong_tenant_agent = {**_AGENT_B, "tenant_id": "other_tenant"}
        r = self._call(agent_record=wrong_tenant_agent)
        assert r["statusCode"] == 404

    def test_agent_not_found_returns_404(self):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.agents_repo") as ar, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = _USER_RESTRICTED
            ar.get.return_value = None
            r = handle_grant_agent_access(TENANT, USER_ID, "agent_b", ADMIN)
        assert r["statusCode"] == 404

    def test_grant_adds_agent_to_list(self):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.agents_repo") as ar, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = _USER_RESTRICTED  # has ["agent_a"]
            ar.get.return_value = _AGENT_B
            r = handle_grant_agent_access(TENANT, USER_ID, "agent_b", ADMIN)
        assert r["statusCode"] == 200
        ur.set_allowed_agents.assert_called_once_with(USER_ID, ["agent_a", "agent_b"])

    def test_grant_already_granted_is_idempotent(self):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.agents_repo") as ar, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = _USER_RESTRICTED  # already has "agent_a"
            ar.get.return_value = _AGENT_A
            r = handle_grant_agent_access(TENANT, USER_ID, "agent_a", ADMIN)
        assert r["statusCode"] == 200
        ur.set_allowed_agents.assert_not_called()  # already present, no write


# ---------------------------------------------------------------------------
# handle_revoke_agent_access
# ---------------------------------------------------------------------------

class TestRevokeAgentAccess:
    def _call(self, user_record=_USER_RESTRICTED, agent_id="agent_a"):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = user_record
            return handle_revoke_agent_access(TENANT, USER_ID, agent_id, ADMIN)

    def test_unauthorized(self):
        r = handle_revoke_agent_access(TENANT, USER_ID, "agent_a", "wrong")
        assert r["statusCode"] == 401

    def test_user_not_found(self):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = None
            r = handle_revoke_agent_access(TENANT, USER_ID, "agent_a", ADMIN)
        assert r["statusCode"] == 404

    def test_wildcard_user_returns_409(self):
        r = self._call(user_record=_USER_WILDCARD)
        assert r["statusCode"] == 409

    def test_unrestricted_null_user_returns_409(self):
        r = self._call(user_record=_USER_UNRESTRICTED)
        assert r["statusCode"] == 409

    def test_agent_not_in_list_returns_404(self):
        r = self._call(agent_id="agent_b")  # user only has agent_a
        assert r["statusCode"] == 404

    def test_revoke_removes_agent_from_list(self):
        user_with_two = {**_USER_RESTRICTED, "allowed_agent_ids": ["agent_a", "agent_b"]}
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = user_with_two
            r = handle_revoke_agent_access(TENANT, USER_ID, "agent_a", ADMIN)
        assert r["statusCode"] == 200
        ur.set_allowed_agents.assert_called_once_with(USER_ID, ["agent_b"])

    def test_revoke_last_agent_results_in_empty_list(self):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = _USER_RESTRICTED  # only has agent_a
            r = handle_revoke_agent_access(TENANT, USER_ID, "agent_a", ADMIN)
        assert r["statusCode"] == 200
        ur.set_allowed_agents.assert_called_once_with(USER_ID, [])
