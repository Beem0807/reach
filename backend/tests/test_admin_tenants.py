import json
from unittest.mock import patch, MagicMock

from shared.exceptions import NameTakenError
from handlers.admin_tenants import (
    handle_create_tenant,
    handle_list_tenants,
    handle_delete_tenant,
    handle_disable_tenant,
    handle_enable_tenant,
    handle_create_tenant_admin_user,
    handle_platform_reset_user_password,
    handle_platform_disable_user,
    handle_platform_set_user_role,
    handle_platform_update_user_name,
)

import conftest
ADMIN = conftest.ADMIN_TOKEN

TENANT = "tenant_1"
USER_ID = "user_1"
_TENANT = {"tenant_id": TENANT, "name": "Acme", "status": "ACTIVE"}
_USER = {"user_id": USER_ID, "tenant_id": TENANT, "name": "Alice", "username": "alice", "role": "admin", "status": "ACTIVE"}


# ---------------------------------------------------------------------------
# handle_create_tenant
# ---------------------------------------------------------------------------

class TestCreateTenant:
    def _call(self, body=None):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            r = handle_create_tenant(body or {}, ADMIN)
        return r, tr

    def test_unauthorized(self):
        r = handle_create_tenant({}, "wrong")
        assert r["statusCode"] == 401

    def test_creates_with_name(self):
        r, tr = self._call({"name": "Acme Corp"})
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert body["name"] == "Acme Corp"
        assert body["tenant_id"].startswith("tenant_")
        tr.create.assert_called_once()

    def test_missing_name_returns_400(self):
        r, _ = self._call({})
        assert r["statusCode"] == 400

    def test_whitespace_only_name_returns_400(self):
        r, _ = self._call({"name": "   "})
        assert r["statusCode"] == 400

    def test_tenant_id_is_unique(self):
        with patch("handlers.admin_tenants.tenants_repo"):
            r1 = handle_create_tenant({"name": "Acme"}, ADMIN)
            r2 = handle_create_tenant({"name": "Beta"}, ADMIN)
        id1 = json.loads(r1["body"])["tenant_id"]
        id2 = json.loads(r2["body"])["tenant_id"]
        assert id1 != id2

    def test_returns_active_status(self):
        r, _ = self._call({"name": "Acme"})
        assert json.loads(r["body"])["status"] == "ACTIVE"

    def test_duplicate_name_returns_409(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.create.side_effect = NameTakenError("Acme")
            r = handle_create_tenant({"name": "Acme"}, ADMIN)
        assert r["statusCode"] == 409


# ---------------------------------------------------------------------------
# handle_list_tenants
# ---------------------------------------------------------------------------

class TestListTenants:
    def test_unauthorized(self):
        r = handle_list_tenants("wrong")
        assert r["statusCode"] == 401

    def test_returns_tenants(self):
        tenants = [{"tenant_id": "t1", "name": "Acme"}, {"tenant_id": "t2", "name": None}]
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.list_all.return_value = tenants
            r = handle_list_tenants(ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["tenants"] == tenants

    def test_returns_empty_list(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.list_all.return_value = []
            r = handle_list_tenants(ADMIN)
        assert json.loads(r["body"])["tenants"] == []

    def _roster(self, n):
        return [{"tenant_id": f"t{i:02d}", "name": f"org-{i:02d}"} for i in range(n)]

    def test_no_limit_returns_all_without_page_meta(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.list_all.return_value = self._roster(30)
            r = handle_list_tenants(ADMIN)
        body = json.loads(r["body"])
        assert len(body["tenants"]) == 30 and "total" not in body

    def test_pagination_returns_page_and_total(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.list_all.return_value = self._roster(30)
            r = handle_list_tenants(ADMIN, limit=20, offset=20)
        body = json.loads(r["body"])
        assert body["total"] == 30 and len(body["tenants"]) == 10
        assert body["tenants"][0]["name"] == "org-20"  # deterministic order

    def test_q_filters_by_name(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.list_all.return_value = [{"tenant_id": "t1", "name": "Acme"}, {"tenant_id": "t2", "name": "Globex"}]
            r = handle_list_tenants(ADMIN, q="glob", limit=20)
        body = json.loads(r["body"])
        assert body["total"] == 1 and body["tenants"][0]["tenant_id"] == "t2"


# ---------------------------------------------------------------------------
# handle_delete_tenant
# ---------------------------------------------------------------------------

class TestDeleteTenant:
    def test_unauthorized(self):
        r = handle_delete_tenant(TENANT, "wrong")
        assert r["statusCode"] == 401

    def test_tenant_not_found(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.get.return_value = None
            r = handle_delete_tenant(TENANT, ADMIN)
        assert r["statusCode"] == 404

    def test_deletes_tenant_cascade(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.get.return_value = _TENANT
            r = handle_delete_tenant(TENANT, ADMIN)
        assert r["statusCode"] == 204
        tr.delete_cascade.assert_called_once_with(TENANT)


# ---------------------------------------------------------------------------
# handle_disable_tenant
# ---------------------------------------------------------------------------

class TestDisableTenant:
    def test_unauthorized(self):
        r = handle_disable_tenant(TENANT, "wrong")
        assert r["statusCode"] == 401

    def test_tenant_not_found(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.get.return_value = None
            r = handle_disable_tenant(TENANT, ADMIN)
        assert r["statusCode"] == 404

    def test_sets_status_to_disabled(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.get.return_value = _TENANT
            r = handle_disable_tenant(TENANT, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["status"] == "DISABLED"
        tr.set_status.assert_called_once_with(TENANT, "DISABLED")


# ---------------------------------------------------------------------------
# handle_enable_tenant
# ---------------------------------------------------------------------------

class TestEnableTenant:
    def test_unauthorized(self):
        r = handle_enable_tenant(TENANT, "wrong")
        assert r["statusCode"] == 401

    def test_tenant_not_found(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.get.return_value = None
            r = handle_enable_tenant(TENANT, ADMIN)
        assert r["statusCode"] == 404

    def test_sets_status_to_active(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.get.return_value = {**_TENANT, "status": "DISABLED"}
            r = handle_enable_tenant(TENANT, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["status"] == "ACTIVE"
        tr.set_status.assert_called_once_with(TENANT, "ACTIVE")


# ---------------------------------------------------------------------------
# handle_create_tenant_admin_user
# ---------------------------------------------------------------------------

class TestCreateTenantAdminUser:
    def _call(self, body, tenant_id=TENANT):
        with patch("handlers.admin_tenants.tenants_repo") as tr, \
             patch("handlers.admin_tenants.users_repo") as ur:
            tr.get.return_value = _TENANT
            ur.get_by_username.return_value = None
            r = handle_create_tenant_admin_user(tenant_id, body, ADMIN)
        return r, ur

    def test_unauthorized(self):
        r = handle_create_tenant_admin_user(TENANT, {"username": "alice"}, "wrong")
        assert r["statusCode"] == 401

    def test_tenant_not_found(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr:
            tr.get.return_value = None
            r = handle_create_tenant_admin_user(TENANT, {"username": "alice"}, ADMIN)
        assert r["statusCode"] == 404

    def test_creates_user_with_temp_password(self):
        r, _ = self._call({"username": "alice", "role": "admin"})
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert body["username"] == "alice"
        assert body["role"] == "admin"
        assert "temp_password" in body
        assert body["must_reset_password"] is True

    def test_missing_username_returns_400(self):
        r, _ = self._call({"role": "admin"})
        assert r["statusCode"] == 400

    def test_invalid_username_chars_returns_400(self):
        r, _ = self._call({"username": "alice-bob"})  # hyphen not allowed
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
        r, _ = self._call({"username": "alice", "role": "superuser"})
        assert r["statusCode"] == 400

    def test_duplicate_username_returns_409(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr, \
             patch("handlers.admin_tenants.users_repo") as ur:
            tr.get.return_value = _TENANT
            ur.get_by_username.return_value = None
            ur.create.side_effect = NameTakenError("alice")
            r = handle_create_tenant_admin_user(TENANT, {"username": "alice"}, ADMIN)
        assert r["statusCode"] == 409

    def test_defaults_role_to_developer(self):
        r, _ = self._call({"username": "bob"})
        assert json.loads(r["body"])["role"] == "developer"

    def test_user_id_starts_with_user(self):
        r, _ = self._call({"username": "alice"})
        assert json.loads(r["body"])["user_id"].startswith("user_")

    def test_all_valid_roles_accepted(self):
        for role in ("admin", "operator", "developer"):
            r, _ = self._call({"username": f"u{role}", "role": role})
            assert r["statusCode"] == 201, f"role {role} should be accepted"

    def test_disabled_tenant_returns_403(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr, \
             patch("handlers.admin_tenants.users_repo"):
            tr.get.return_value = {**_TENANT, "status": "DISABLED"}
            r = handle_create_tenant_admin_user(TENANT, {"username": "alice"}, ADMIN)
        assert r["statusCode"] == 403

    def test_active_tenant_allows_creation(self):
        with patch("handlers.admin_tenants.tenants_repo") as tr, \
             patch("handlers.admin_tenants.users_repo") as ur:
            tr.get.return_value = _TENANT  # status=ACTIVE
            ur.get_by_username.return_value = None
            r = handle_create_tenant_admin_user(TENANT, {"username": "alice"}, ADMIN)
        assert r["statusCode"] == 201


# ---------------------------------------------------------------------------
# handle_platform_reset_user_password
# ---------------------------------------------------------------------------

class TestPlatformResetUserPassword:
    def test_unauthorized(self):
        r = handle_platform_reset_user_password(TENANT, USER_ID, "wrong")
        assert r["statusCode"] == 401

    def test_user_not_found(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = None
            r = handle_platform_reset_user_password(TENANT, USER_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_user_from_different_tenant_returns_404(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = {**_USER, "tenant_id": "other"}
            r = handle_platform_reset_user_password(TENANT, USER_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_returns_temp_password(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = _USER
            r = handle_platform_reset_user_password(TENANT, USER_ID, ADMIN)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert "temp_password" in body
        assert body["must_reset_password"] is True
        ur.update_password.assert_called_once()


# ---------------------------------------------------------------------------
# handle_platform_disable_user
# ---------------------------------------------------------------------------

class TestPlatformDisableUser:
    def test_unauthorized(self):
        r = handle_platform_disable_user(TENANT, USER_ID, "wrong")
        assert r["statusCode"] == 401

    def test_user_not_found(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = None
            r = handle_platform_disable_user(TENANT, USER_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_user_from_different_tenant_returns_404(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = {**_USER, "tenant_id": "other"}
            r = handle_platform_disable_user(TENANT, USER_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_disables_user(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = _USER
            r = handle_platform_disable_user(TENANT, USER_ID, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["status"] == "REVOKED"
        ur.disable.assert_called_once()


# ---------------------------------------------------------------------------
# handle_platform_set_user_role
# ---------------------------------------------------------------------------

class TestPlatformSetUserRole:
    def test_unauthorized(self):
        r = handle_platform_set_user_role(TENANT, USER_ID, {"role": "operator"}, "wrong")
        assert r["statusCode"] == 401

    def test_invalid_role_returns_400(self):
        with patch("handlers.admin_tenants.users_repo"):
            r = handle_platform_set_user_role(TENANT, USER_ID, {"role": "god"}, ADMIN)
        assert r["statusCode"] == 400

    def test_user_not_found(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = None
            r = handle_platform_set_user_role(TENANT, USER_ID, {"role": "operator"}, ADMIN)
        assert r["statusCode"] == 404

    def test_user_from_different_tenant_returns_404(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = {**_USER, "tenant_id": "other"}
            r = handle_platform_set_user_role(TENANT, USER_ID, {"role": "operator"}, ADMIN)
        assert r["statusCode"] == 404

    def test_sets_role(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = _USER
            r = handle_platform_set_user_role(TENANT, USER_ID, {"role": "operator"}, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["role"] == "operator"
        ur.set_role.assert_called_once_with(USER_ID, "operator")

    def test_all_valid_roles_accepted(self):
        for role in ("admin", "operator", "developer"):
            with patch("handlers.admin_tenants.users_repo") as ur:
                ur.get.return_value = _USER
                r = handle_platform_set_user_role(TENANT, USER_ID, {"role": role}, ADMIN)
            assert r["statusCode"] == 200


# ---------------------------------------------------------------------------
# handle_platform_update_user_name
# ---------------------------------------------------------------------------

class TestPlatformUpdateUserName:
    def test_unauthorized(self):
        r = handle_platform_update_user_name(TENANT, USER_ID, {"name": "Bob"}, "wrong")
        assert r["statusCode"] == 401

    def test_missing_name_returns_400(self):
        with patch("handlers.admin_tenants.users_repo"):
            r = handle_platform_update_user_name(TENANT, USER_ID, {}, ADMIN)
        assert r["statusCode"] == 400

    def test_whitespace_name_returns_400(self):
        with patch("handlers.admin_tenants.users_repo"):
            r = handle_platform_update_user_name(TENANT, USER_ID, {"name": "  "}, ADMIN)
        assert r["statusCode"] == 400

    def test_user_not_found(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = None
            r = handle_platform_update_user_name(TENANT, USER_ID, {"name": "Bob"}, ADMIN)
        assert r["statusCode"] == 404

    def test_user_from_different_tenant_returns_404(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = {**_USER, "tenant_id": "other"}
            r = handle_platform_update_user_name(TENANT, USER_ID, {"name": "Bob"}, ADMIN)
        assert r["statusCode"] == 404

    def test_updates_name(self):
        with patch("handlers.admin_tenants.users_repo") as ur:
            ur.get.return_value = _USER
            r = handle_platform_update_user_name(TENANT, USER_ID, {"name": "Bob"}, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["name"] == "Bob"
        ur.update_name.assert_called_once_with(USER_ID, "Bob")
