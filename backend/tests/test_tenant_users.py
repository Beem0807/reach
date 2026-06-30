"""Tests for tenant user management (create, disable, role, password reset)."""
import json
from unittest.mock import patch

from handlers.tenant_users import (
    handle_list_tenant_users,
    handle_create_tenant_user,
    handle_disable_tenant_user,
    handle_enable_tenant_user,
    handle_delete_tenant_user,
    handle_set_user_role,
    handle_reset_user_password,
    handle_get_user_agents,
    handle_set_user_agents,
    list_users_handler,
    create_user_handler,
    disable_user_handler,
    enable_user_handler,
    set_role_handler,
    reset_password_handler,
    get_user_agents_handler,
    set_user_agents_handler,
)
from shared.tenant_auth import create_tenant_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ADMIN_TOKEN = {"sub": "user_admin", "tenant_id": "tenant_acme", "role": "admin", "username": "admin"}
USER_TOKEN  = {"sub": "user_user",  "tenant_id": "tenant_acme", "role": "developer",  "username": "alice"}

EXISTING_USER = {
    "user_id":    "user_bob",
    "tenant_id":  "tenant_acme",
    "username":   "bob",
    "name":       "Bob",
    "role":       "developer",
    "status":     "ACTIVE",
    "must_reset_password": False,
    "last_login_at": None,
    "disabled_at": None,
    "created_at": "2026-01-01T00:00:00",
}


# ---------------------------------------------------------------------------
# List users
# ---------------------------------------------------------------------------
class TestListTenantUsers:
    def test_admin_can_list(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.list_by_tenant.return_value = [EXISTING_USER]
            r = handle_list_tenant_users(ADMIN_TOKEN)
        assert r["statusCode"] == 200
        assert len(json.loads(r["body"])["users"]) == 1

    def test_non_admin_rejected(self):
        r = handle_list_tenant_users(USER_TOKEN)
        assert r["statusCode"] == 403


# ---------------------------------------------------------------------------
# Create user
# ---------------------------------------------------------------------------
class TestCreateTenantUser:
    def _call(self, body, token=ADMIN_TOKEN):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit"):
            ur.get_by_username.return_value = None
            ur.create.return_value = None
            # The acting admin's own record (unrestricted by default).
            ur.get.return_value = {"user_id": token["sub"], "tenant_id": token["tenant_id"],
                                   "role": "admin", "allowed_agent_ids": None}
            return handle_create_tenant_user(body, token), ur

    def test_admin_creates_user(self):
        r, _ = self._call({"username": "carol", "role": "developer"})
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert body["username"] == "carol"
        assert "temp_password" in body
        assert body["must_reset_password"] is True

    def test_defaults_to_tenant_user_role(self):
        r, ur = self._call({"username": "dave"})
        args = ur.create.call_args[0][0]
        assert args["role"] == "developer"

    def test_can_create_admin(self):
        r, ur = self._call({"username": "eve", "role": "admin"})
        args = ur.create.call_args[0][0]
        assert args["role"] == "admin"

    def test_non_admin_rejected(self):
        r, _ = self._call({"username": "eve"}, token=USER_TOKEN)
        assert r["statusCode"] == 403

    def test_restricted_admin_cannot_grant_agent_outside_scope(self):
        # Admin scoped to agent_a tries to grant a new user agent_b → 403.
        restricted_admin = {"user_id": "user_admin", "tenant_id": "tenant_acme",
                            "role": "admin", "allowed_agent_ids": ["agent_a"]}
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.agents_repo") as agr, \
             patch("handlers.tenant_users.audit"):
            ur.get_by_username.return_value = None
            ur.get.return_value = restricted_admin
            agr.list_by_tenant.return_value = [
                {"agent_id": "agent_a", "tenant_id": "tenant_acme"},
                {"agent_id": "agent_b", "tenant_id": "tenant_acme"},
            ]
            r = handle_create_tenant_user(
                {"username": "carol", "allowed_agent_ids": ["agent_b"]}, ADMIN_TOKEN)
        assert r["statusCode"] == 403
        ur.create.assert_not_called()

    def test_restricted_admin_cannot_grant_all_agents(self):
        # Granting null (tenant-wide) while restricted is an escalation → 403.
        restricted_admin = {"user_id": "user_admin", "tenant_id": "tenant_acme",
                            "role": "admin", "allowed_agent_ids": ["agent_a"]}
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.agents_repo") as agr, \
             patch("handlers.tenant_users.audit"):
            ur.get_by_username.return_value = None
            ur.get.return_value = restricted_admin
            agr.list_by_tenant.return_value = [{"agent_id": "agent_a", "tenant_id": "tenant_acme"}]
            r = handle_create_tenant_user({"username": "carol"}, ADMIN_TOKEN)  # no allowed_agent_ids = null
        assert r["statusCode"] == 403

    def test_cannot_create_scoped_admin(self):
        # Admins are always tenant-wide - a scoped admin is rejected.
        r, ur = self._call({"username": "adminx", "role": "admin", "allowed_agent_ids": ["agent_a"]})
        assert r["statusCode"] == 400
        ur.create.assert_not_called()

    def test_can_create_unscoped_admin(self):
        r, ur = self._call({"username": "adminy", "role": "admin"})
        assert r["statusCode"] == 201
        assert ur.create.call_args[0][0]["allowed_agent_ids"] is None

    def test_restricted_admin_can_grant_within_scope(self):
        restricted_admin = {"user_id": "user_admin", "tenant_id": "tenant_acme",
                            "role": "admin", "allowed_agent_ids": ["agent_a", "agent_b"]}
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.agents_repo") as agr, \
             patch("handlers.tenant_users.audit"):
            ur.get_by_username.return_value = None
            ur.create.return_value = None
            ur.get.return_value = restricted_admin
            agr.list_by_tenant.return_value = [
                {"agent_id": "agent_a", "tenant_id": "tenant_acme"},
                {"agent_id": "agent_b", "tenant_id": "tenant_acme"},
            ]
            r = handle_create_tenant_user(
                {"username": "carol", "allowed_agent_ids": ["agent_a"]}, ADMIN_TOKEN)
        assert r["statusCode"] == 201
        ur.create.assert_called_once()

    def test_missing_username_returns_400(self):
        r, _ = self._call({})
        assert r["statusCode"] == 400

    def test_invalid_username_chars_returns_400(self):
        r, _ = self._call({"username": "alice-bob"})
        assert r["statusCode"] == 400

    def test_username_too_short_returns_400(self):
        r, _ = self._call({"username": "a"})
        assert r["statusCode"] == 400
        assert "2" in json.loads(r["body"])["error"]

    def test_username_min_length_accepted(self):
        r, _ = self._call({"username": "ab"})
        assert r["statusCode"] == 201

    def test_username_max_length_accepted(self):
        r, _ = self._call({"username": "a" * 32})
        assert r["statusCode"] == 201

    def test_username_too_long_returns_400(self):
        r, _ = self._call({"username": "a" * 33})
        assert r["statusCode"] == 400
        assert "32" in json.loads(r["body"])["error"]

    def test_invalid_role_returns_400(self):
        r, _ = self._call({"username": "frank", "role": "SUPERUSER"})
        assert r["statusCode"] == 400

    def test_duplicate_username_returns_409(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit"):
            ur.get_by_username.return_value = EXISTING_USER
            ur.get.return_value = {"user_id": "user_admin", "role": "admin", "allowed_agent_ids": None}
            r = handle_create_tenant_user({"username": "bob"}, ADMIN_TOKEN)
        assert r["statusCode"] == 409

    def test_default_allowed_agent_ids_is_none(self):
        _, ur = self._call({"username": "carol"})
        stored = ur.create.call_args[0][0]
        assert stored["allowed_agent_ids"] is None

    def test_allowed_agent_ids_list_stored_on_create(self):
        _, ur = self._call({"username": "carol", "allowed_agent_ids": ["agent_x", "agent_y"]})
        stored = ur.create.call_args[0][0]
        assert stored["allowed_agent_ids"] == ["agent_x", "agent_y"]

    def test_allowed_agent_ids_empty_list_stored(self):
        _, ur = self._call({"username": "carol", "allowed_agent_ids": []})
        stored = ur.create.call_args[0][0]
        assert stored["allowed_agent_ids"] == []

    def test_allowed_agent_ids_null_stored(self):
        _, ur = self._call({"username": "carol", "allowed_agent_ids": None})
        stored = ur.create.call_args[0][0]
        assert stored["allowed_agent_ids"] is None

    def test_allowed_agent_ids_non_list_returns_400(self):
        r, _ = self._call({"username": "carol", "allowed_agent_ids": "agent_x"})
        assert r["statusCode"] == 400

    def test_allowed_agent_ids_non_string_items_returns_400(self):
        r, _ = self._call({"username": "carol", "allowed_agent_ids": [1, 2, 3]})
        assert r["statusCode"] == 400

    def test_allowed_agent_ids_in_audit_metadata(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit") as mock_audit:
            ur.get_by_username.return_value = None
            ur.create.return_value = None
            ur.get.return_value = {"user_id": "user_admin", "role": "admin", "allowed_agent_ids": None}
            handle_create_tenant_user({"username": "carol", "allowed_agent_ids": ["agent_x"]}, ADMIN_TOKEN)
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["allowed_agent_ids"] == ["agent_x"]

    def test_null_allowed_agent_ids_in_audit_metadata(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit") as mock_audit:
            ur.get_by_username.return_value = None
            ur.create.return_value = None
            ur.get.return_value = {"user_id": "user_admin", "role": "admin", "allowed_agent_ids": None}
            handle_create_tenant_user({"username": "carol"}, ADMIN_TOKEN)
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["allowed_agent_ids"] is None


# ---------------------------------------------------------------------------
# Disable user
# ---------------------------------------------------------------------------
class TestDisableTenantUser:
    def test_admin_can_disable(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit"):
            ur.get.return_value = EXISTING_USER
            ur.disable.return_value = None
            r = handle_disable_tenant_user("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["status"] == "REVOKED"

    def test_cannot_disable_self(self):
        self_token = {**ADMIN_TOKEN, "sub": "user_bob"}
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = EXISTING_USER
            r = handle_disable_tenant_user("user_bob", self_token)
        assert r["statusCode"] == 409

    def test_user_not_found_returns_404(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = None
            r = handle_disable_tenant_user("user_missing", ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_cross_tenant_returns_404(self):
        other_tenant_user = {**EXISTING_USER, "tenant_id": "tenant_other"}
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = other_tenant_user
            r = handle_disable_tenant_user("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_non_admin_rejected(self):
        r = handle_disable_tenant_user("user_bob", USER_TOKEN)
        assert r["statusCode"] == 403


# ---------------------------------------------------------------------------
# Enable user
# ---------------------------------------------------------------------------
DISABLED_USER = {**EXISTING_USER, "status": "REVOKED"}


class TestEnableTenantUser:
    def test_admin_can_enable_disabled_user(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit"):
            ur.get.return_value = DISABLED_USER
            ur.enable.return_value = None
            r = handle_enable_tenant_user("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["status"] == "ACTIVE"
        ur.enable.assert_called_once_with("user_bob")

    def test_writes_audit_event(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit") as mock_audit:
            ur.get.return_value = DISABLED_USER
            ur.enable.return_value = None
            handle_enable_tenant_user("user_bob", ADMIN_TOKEN)
        mock_audit.write.assert_called_once()
        assert mock_audit.write.call_args[0][0] == "user.enabled"
        assert mock_audit.write.call_args[1]["resource_id"] == "user_bob"

    def test_already_active_returns_409(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = EXISTING_USER  # status ACTIVE
            r = handle_enable_tenant_user("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 409

    def test_user_not_found_returns_404(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = None
            r = handle_enable_tenant_user("user_missing", ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_cross_tenant_returns_404(self):
        other = {**DISABLED_USER, "tenant_id": "tenant_other"}
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = other
            r = handle_enable_tenant_user("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_non_admin_rejected(self):
        r = handle_enable_tenant_user("user_bob", USER_TOKEN)
        assert r["statusCode"] == 403


class TestDeleteTenantUser:
    def test_admin_can_delete_disabled_user(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.api_tokens_repo") as tr, \
             patch("handlers.tenant_users.audit"):
            ur.get.return_value = DISABLED_USER
            tr.list_by_user.return_value = []
            r = handle_delete_tenant_user("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["deleted"] is True
        ur.delete.assert_called_once_with("user_bob")

    def test_deletes_user_api_tokens(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.api_tokens_repo") as tr, \
             patch("handlers.tenant_users.audit"):
            ur.get.return_value = DISABLED_USER
            tr.list_by_user.return_value = [{"token_id": "t1"}, {"token_id": "t2"}]
            handle_delete_tenant_user("user_bob", ADMIN_TOKEN)
        assert tr.delete.call_count == 2

    def test_active_user_cannot_be_deleted_409(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.api_tokens_repo") as tr:
            ur.get.return_value = EXISTING_USER  # ACTIVE
            r = handle_delete_tenant_user("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 409
        ur.delete.assert_not_called()

    def test_cannot_delete_self(self):
        self_token = {**ADMIN_TOKEN, "sub": "user_bob"}
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = DISABLED_USER
            r = handle_delete_tenant_user("user_bob", self_token)
        assert r["statusCode"] == 409

    def test_writes_audit_event(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.api_tokens_repo") as tr, \
             patch("handlers.tenant_users.audit") as mock_audit:
            ur.get.return_value = DISABLED_USER
            tr.list_by_user.return_value = []
            handle_delete_tenant_user("user_bob", ADMIN_TOKEN)
        assert mock_audit.write.call_args[0][0] == "user.deleted"
        assert mock_audit.write.call_args[1]["resource_id"] == "user_bob"

    def test_user_not_found_returns_404(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = None
            r = handle_delete_tenant_user("user_missing", ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_cross_tenant_returns_404(self):
        other = {**DISABLED_USER, "tenant_id": "tenant_other"}
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = other
            r = handle_delete_tenant_user("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_non_admin_rejected(self):
        r = handle_delete_tenant_user("user_bob", USER_TOKEN)
        assert r["statusCode"] == 403


# ---------------------------------------------------------------------------
# Set role
# ---------------------------------------------------------------------------
class TestSetUserRole:
    def test_promote_to_admin(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit"):
            ur.get.return_value = EXISTING_USER
            ur.set_role.return_value = None
            r = handle_set_user_role("user_bob", {"role": "admin"}, ADMIN_TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["role"] == "admin"

    def test_promote_to_admin_clears_agent_scope(self):
        scoped = {**EXISTING_USER, "role": "operator", "allowed_agent_ids": ["agent_a"]}
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit"):
            ur.get.return_value = scoped
            ur.set_role.return_value = None
            r = handle_set_user_role("user_bob", {"role": "admin"}, ADMIN_TOKEN)
        assert r["statusCode"] == 200
        ur.set_allowed_agents.assert_called_once_with("user_bob", None)

    def test_demote_to_user(self):
        admin_user = {**EXISTING_USER, "role": "admin"}
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit"):
            ur.get.return_value = admin_user
            r = handle_set_user_role("user_bob", {"role": "developer"}, ADMIN_TOKEN)
        assert r["statusCode"] == 200

    def test_invalid_role_returns_400(self):
        r = handle_set_user_role("user_bob", {"role": "GOD"}, ADMIN_TOKEN)
        assert r["statusCode"] == 400

    def test_non_admin_rejected(self):
        r = handle_set_user_role("user_bob", {"role": "admin"}, USER_TOKEN)
        assert r["statusCode"] == 403

    def test_user_not_found_returns_404(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = None
            r = handle_set_user_role("user_missing", {"role": "operator"}, ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_cross_tenant_user_returns_404(self):
        other_tenant_user = {**EXISTING_USER, "tenant_id": "tenant_other"}
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = other_tenant_user
            r = handle_set_user_role("user_bob", {"role": "operator"}, ADMIN_TOKEN)
        assert r["statusCode"] == 404


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------
class TestResetUserPassword:
    def test_admin_can_reset(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit"):
            ur.get.return_value = EXISTING_USER
            ur.update_password.return_value = None
            r = handle_reset_user_password("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert "temp_password" in body
        assert body["must_reset_password"] is True

    def test_non_admin_rejected(self):
        r = handle_reset_user_password("user_bob", USER_TOKEN)
        assert r["statusCode"] == 403

    def test_user_not_found_returns_404(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = None
            r = handle_reset_user_password("user_missing", ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_cross_tenant_user_returns_404(self):
        other_tenant_user = {**EXISTING_USER, "tenant_id": "tenant_other"}
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = other_tenant_user
            r = handle_reset_user_password("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 404


# ---------------------------------------------------------------------------
# handle_get_user_agents
# ---------------------------------------------------------------------------
class TestGetUserAgents:
    def test_admin_gets_allowed_agents(self):
        user = {**EXISTING_USER, "allowed_agent_ids": ["agent_1", "agent_2"]}
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = user
            r = handle_get_user_agents("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["allowed_agent_ids"] == ["agent_1", "agent_2"]

    def test_non_admin_forbidden(self):
        r = handle_get_user_agents("user_bob", USER_TOKEN)
        assert r["statusCode"] == 403

    def test_user_not_found_returns_404(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = None
            r = handle_get_user_agents("user_missing", ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_cross_tenant_returns_404(self):
        other_tenant_user = {**EXISTING_USER, "tenant_id": "tenant_other"}
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = other_tenant_user
            r = handle_get_user_agents("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_null_allowed_agent_ids_means_all(self):
        user = {**EXISTING_USER, "allowed_agent_ids": None}
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = user
            r = handle_get_user_agents("user_bob", ADMIN_TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["allowed_agent_ids"] is None


# ---------------------------------------------------------------------------
# handle_set_user_agents
# ---------------------------------------------------------------------------
class TestSetUserAgents:
    def test_admin_sets_allowed_agents(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = EXISTING_USER
            ur.set_allowed_agents.return_value = None
            r = handle_set_user_agents("user_bob", {"allowed_agent_ids": ["agent_x"]}, ADMIN_TOKEN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["allowed_agent_ids"] == ["agent_x"]
        ur.set_allowed_agents.assert_called_once_with("user_bob", ["agent_x"])

    def test_non_admin_forbidden(self):
        r = handle_set_user_agents("user_bob", {}, USER_TOKEN)
        assert r["statusCode"] == 403

    def test_user_not_found_returns_404(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = None
            r = handle_set_user_agents("user_missing", {"allowed_agent_ids": []}, ADMIN_TOKEN)
        assert r["statusCode"] == 404

    def test_null_agent_ids_allows_all(self):
        with patch("handlers.tenant_users.users_repo") as ur:
            ur.get.return_value = EXISTING_USER
            ur.set_allowed_agents.return_value = None
            r = handle_set_user_agents("user_bob", {"allowed_agent_ids": None}, ADMIN_TOKEN)
        assert r["statusCode"] == 200
        ur.set_allowed_agents.assert_called_once_with("user_bob", None)


# ---------------------------------------------------------------------------
# handle_set_user_agents - audit
# ---------------------------------------------------------------------------

def _set_agents(prev_ids, new_ids, user=None):
    """Helper: call handle_set_user_agents with audit mocked, return (response, audit_mock)."""
    target = {**EXISTING_USER, "allowed_agent_ids": prev_ids}
    if user is None:
        user = target
    admin_actor = {"user_id": "user_admin", "tenant_id": "tenant_acme", "role": "admin", "allowed_agent_ids": None}
    with patch("handlers.tenant_users.users_repo") as ur, \
         patch("handlers.tenant_users.audit") as mock_audit:
        # The actor (ADMIN_TOKEN.sub) is a distinct unrestricted admin; user_bob is the target.
        ur.get.side_effect = lambda uid: admin_actor if uid == "user_admin" else target
        ur.set_allowed_agents.return_value = None
        r = handle_set_user_agents("user_bob", {"allowed_agent_ids": new_ids}, ADMIN_TOKEN)
    return r, mock_audit


class TestSetUserAgentsScope:
    def test_restricted_admin_cannot_widen_target_beyond_own_scope(self):
        restricted_admin = {"user_id": "user_admin", "tenant_id": "tenant_acme",
                            "role": "admin", "allowed_agent_ids": ["agent_a"]}
        target = {**EXISTING_USER, "allowed_agent_ids": ["agent_a"]}
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.agents_repo") as agr, \
             patch("handlers.tenant_users.audit"):
            ur.get.side_effect = lambda uid: restricted_admin if uid == "user_admin" else target
            agr.list_by_tenant.return_value = [
                {"agent_id": "agent_a", "tenant_id": "tenant_acme"},
                {"agent_id": "agent_b", "tenant_id": "tenant_acme"},
            ]
            r = handle_set_user_agents("user_bob", {"allowed_agent_ids": ["agent_a", "agent_b"]}, ADMIN_TOKEN)
        assert r["statusCode"] == 403
        ur.set_allowed_agents.assert_not_called()

    def test_restricted_admin_cannot_widen_self(self):
        # Actor == target: a restricted admin cannot grant themselves all agents (null).
        restricted_admin = {"user_id": "user_admin", "tenant_id": "tenant_acme",
                            "role": "admin", "allowed_agent_ids": ["agent_a"]}
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.agents_repo") as agr, \
             patch("handlers.tenant_users.audit"):
            ur.get.return_value = restricted_admin
            agr.list_by_tenant.return_value = [{"agent_id": "agent_a", "tenant_id": "tenant_acme"}]
            r = handle_set_user_agents("user_admin", {"allowed_agent_ids": None}, ADMIN_TOKEN)
        assert r["statusCode"] == 403
        ur.set_allowed_agents.assert_not_called()

    def test_invalid_shape_returns_400(self):
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit"):
            ur.get.return_value = {**EXISTING_USER}
            r = handle_set_user_agents("user_bob", {"allowed_agent_ids": "nope"}, ADMIN_TOKEN)
        assert r["statusCode"] == 400

    def test_cannot_scope_an_admin_target(self):
        admin_target = {**EXISTING_USER, "user_id": "user_target", "role": "admin", "allowed_agent_ids": None}
        actor = {"user_id": "user_admin", "tenant_id": "tenant_acme", "role": "admin", "allowed_agent_ids": None}
        with patch("handlers.tenant_users.users_repo") as ur, \
             patch("handlers.tenant_users.audit"):
            ur.get.side_effect = lambda uid: actor if uid == "user_admin" else admin_target
            r = handle_set_user_agents("user_target", {"allowed_agent_ids": ["agent_a"]}, ADMIN_TOKEN)
        assert r["statusCode"] == 400
        ur.set_allowed_agents.assert_not_called()


class TestSetUserAgentsAudit:
    def test_always_writes_audit(self):
        _, mock_audit = _set_agents(None, ["agent_x"])
        mock_audit.write.assert_called_once()
        assert mock_audit.write.call_args[0][0] == "user.agents_changed"

    def test_audit_resource_id_is_target_user(self):
        _, mock_audit = _set_agents(None, ["agent_x"])
        assert mock_audit.write.call_args[1]["resource_id"] == "user_bob"

    def test_audit_resource_type_is_user(self):
        _, mock_audit = _set_agents(None, ["agent_x"])
        assert mock_audit.write.call_args[1]["resource_type"] == "user"

    def test_metadata_contains_target_username(self):
        _, mock_audit = _set_agents(None, ["agent_x"])
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["target_username"] == "bob"

    def test_metadata_previous_and_current_recorded(self):
        _, mock_audit = _set_agents(["agent_a"], ["agent_x"])
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["previous"] == ["agent_a"]
        assert meta["current"] == ["agent_x"]

    def test_added_computed_when_both_lists(self):
        _, mock_audit = _set_agents(["agent_a"], ["agent_a", "agent_b"])
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["added"] == ["agent_b"]
        assert meta["removed"] == []

    def test_removed_computed_when_both_lists(self):
        _, mock_audit = _set_agents(["agent_a", "agent_b"], ["agent_a"])
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["removed"] == ["agent_b"]
        assert meta["added"] == []

    def test_added_and_removed_both_null_when_prev_was_none(self):
        # prev=None means "all agents" - can't compute a diff
        _, mock_audit = _set_agents(None, ["agent_x"])
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["previous"] is None
        assert meta["added"] is None
        assert meta["removed"] is None

    def test_added_and_removed_both_null_when_new_is_none(self):
        # new=None means "all agents" - can't compute a diff
        _, mock_audit = _set_agents(["agent_a"], None)
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["current"] is None
        assert meta["added"] is None
        assert meta["removed"] is None

    def test_none_to_none_still_writes_audit(self):
        # unrestricted → unrestricted: admin explicitly set it; still audited
        _, mock_audit = _set_agents(None, None)
        mock_audit.write.assert_called_once()

    def test_empty_list_recorded_as_no_agents(self):
        # [] means user is locked out of all agents - current recorded as []
        _, mock_audit = _set_agents(["agent_a"], [])
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["current"] == []
        assert meta["removed"] == ["agent_a"]
        assert meta["added"] == []

    def test_empty_to_list_records_added(self):
        _, mock_audit = _set_agents([], ["agent_a", "agent_b"])
        meta = mock_audit.write.call_args[1]["metadata"]
        assert meta["added"] == ["agent_a", "agent_b"]
        assert meta["removed"] == []

    def test_audit_actor_id_from_token(self):
        _, mock_audit = _set_agents(None, ["agent_x"])
        assert mock_audit.write.call_args[1]["actor_id"] == ADMIN_TOKEN.get("sub") or ADMIN_TOKEN.get("user_id", "")


# ---------------------------------------------------------------------------
# Lambda handler wrappers
# ---------------------------------------------------------------------------

_VALID_TOKEN = create_tenant_token(
    user_id="user_admin",
    tenant_id="tenant_acme",
    role="admin",
    username="admin",
)

_OK = {"statusCode": 200, "headers": {}, "body": "{}"}
_ACTIVE_TENANT = {"tenant_id": "tenant_acme", "name": "Acme", "status": "ACTIVE"}


def _evt(headers=None, body=None, path=None, qs=None):
    return {
        "headers": headers if headers is not None else {"authorization": f"Bearer {_VALID_TOKEN}"},
        "body": body,
        "pathParameters": path or {},
        "queryStringParameters": qs or {},
    }


class TestListUsersHandler:
    def test_missing_auth_returns_401(self):
        r = list_users_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_invalid_token_returns_401(self):
        r = list_users_handler(_evt(headers={"authorization": "Bearer bad"}), None)
        assert r["statusCode"] == 401

    def test_delegates(self):
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_users.handle_list_tenant_users", return_value=_OK) as h:
            list_users_handler(_evt(), None)
        h.assert_called_once_with(ADMIN_TOKEN)


class TestCreateUserHandler:
    def test_missing_auth_returns_401(self):
        r = create_user_handler(_evt(headers={}, body="{}"), None)
        assert r["statusCode"] == 401

    def test_invalid_json_returns_400(self):
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN):
            r = create_user_handler(_evt(body="not-json"), None)
        assert r["statusCode"] == 400

    def test_delegates(self):
        body = {"username": "carol", "role": "developer"}
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_users.handle_create_tenant_user", return_value=_OK) as h:
            create_user_handler(_evt(body=json.dumps(body)), None)
        h.assert_called_once()
        assert h.call_args[0][0] == body


class TestDisableUserHandler:
    def test_missing_auth_returns_401(self):
        r = disable_user_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delegates_with_user_id_from_path(self):
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_users.handle_disable_tenant_user", return_value=_OK) as h:
            disable_user_handler(_evt(path={"user_id": "user_bob"}), None)
        h.assert_called_once()
        assert h.call_args[0][0] == "user_bob"


class TestEnableUserHandler:
    def test_missing_auth_returns_401(self):
        r = enable_user_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delegates_with_user_id_from_path(self):
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_users.handle_enable_tenant_user", return_value=_OK) as h:
            enable_user_handler(_evt(path={"user_id": "user_bob"}), None)
        h.assert_called_once()
        assert h.call_args[0][0] == "user_bob"


class TestSetRoleHandler:
    def test_missing_auth_returns_401(self):
        r = set_role_handler(_evt(headers={}, body="{}"), None)
        assert r["statusCode"] == 401

    def test_invalid_json_returns_400(self):
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN):
            r = set_role_handler(_evt(path={"user_id": "user_bob"}, body="bad-json"), None)
        assert r["statusCode"] == 400

    def test_delegates_with_user_id_and_body(self):
        body = {"role": "operator"}
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_users.handle_set_user_role", return_value=_OK) as h:
            set_role_handler(_evt(path={"user_id": "user_bob"}, body=json.dumps(body)), None)
        h.assert_called_once()
        assert h.call_args[0][0] == "user_bob"
        assert h.call_args[0][1] == body


class TestResetPasswordHandler:
    def test_missing_auth_returns_401(self):
        r = reset_password_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delegates_with_user_id_from_path(self):
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_users.handle_reset_user_password", return_value=_OK) as h:
            reset_password_handler(_evt(path={"user_id": "user_bob"}), None)
        h.assert_called_once()
        assert h.call_args[0][0] == "user_bob"


class TestGetUserAgentsHandler:
    def test_missing_auth_returns_401(self):
        r = get_user_agents_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_invalid_token_returns_401(self):
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=None):
            r = get_user_agents_handler(_evt(headers={"authorization": "Bearer bad"}), None)
        assert r["statusCode"] == 401

    def test_delegates_with_user_id_and_payload(self):
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_users.handle_get_user_agents", return_value=_OK) as h:
            r = get_user_agents_handler(_evt(path={"user_id": "user_bob"}), None)
        h.assert_called_once()
        assert h.call_args[0][0] == "user_bob"
        assert h.call_args[0][1] == ADMIN_TOKEN
        assert r == _OK


class TestSetUserAgentsHandler:
    def test_missing_auth_returns_401(self):
        r = set_user_agents_handler(_evt(headers={}, body="{}"), None)
        assert r["statusCode"] == 401

    def test_invalid_json_returns_400(self):
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN):
            r = set_user_agents_handler(_evt(path={"user_id": "user_bob"}, body="bad-json"), None)
        assert r["statusCode"] == 400

    def test_delegates_with_user_id_body_and_payload(self):
        body = {"allowed_agent_ids": ["agent_x", "agent_y"]}
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_users.handle_set_user_agents", return_value=_OK) as h:
            r = set_user_agents_handler(_evt(path={"user_id": "user_bob"}, body=json.dumps(body)), None)
        h.assert_called_once()
        assert h.call_args[0][0] == "user_bob"
        assert h.call_args[0][1] == body
        assert h.call_args[0][2] == ADMIN_TOKEN
        assert r == _OK

    def test_empty_body_defaults_to_empty_dict(self):
        with patch("handlers.tenant_users._verify_tenant_payload", return_value=ADMIN_TOKEN), \
             patch("handlers.tenant_users.handle_set_user_agents", return_value=_OK) as h:
            set_user_agents_handler(_evt(path={"user_id": "user_bob"}, body=None), None)
        assert h.call_args[0][1] == {}
