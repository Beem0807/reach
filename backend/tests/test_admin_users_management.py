import json
from unittest.mock import patch

from handlers.admin_users import (
    handle_create_user,
    handle_delete_user,
    handle_list_users,
    handle_rotate_user_token,
)

ADMIN = "test-admin-token"
TENANT = "tenant_1"
USER_ID = "user_1"
API_URL = "https://api.example.com"

_TENANT = {"tenant_id": TENANT}
_USER = {"user_id": USER_ID, "tenant_id": TENANT, "name": "alice", "created_at": "2026-01-01T00:00:00+00:00"}


class TestCreateUser:
    def _call(self, body=None, tenant_exists=True):
        with patch("handlers.admin_users.tenants_repo") as tr, \
             patch("handlers.admin_users.users_repo") as ur:
            tr.get.return_value = _TENANT if tenant_exists else None
            return handle_create_user(TENANT, body or {}, ADMIN, API_URL)

    def test_unauthorized(self):
        r = handle_create_user(TENANT, {}, "wrong", API_URL)
        assert r["statusCode"] == 401

    def test_tenant_not_found(self):
        r = self._call(tenant_exists=False)
        assert r["statusCode"] == 404

    def test_creates_user_with_name(self):
        r = self._call({"name": "alice"})
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert body["name"] == "alice"
        assert body["token"].startswith("tok_")
        assert body["user_id"].startswith("user_")
        assert "cli_login" in body["commands"]

    def test_creates_user_without_name(self):
        r = self._call({})
        assert r["statusCode"] == 201
        assert json.loads(r["body"])["name"] is None

    def test_token_is_unique(self):
        with patch("handlers.admin_users.tenants_repo") as tr, \
             patch("handlers.admin_users.users_repo"):
            tr.get.return_value = _TENANT
            r1 = handle_create_user(TENANT, {}, ADMIN, API_URL)
            r2 = handle_create_user(TENANT, {}, ADMIN, API_URL)
        assert json.loads(r1["body"])["token"] != json.loads(r2["body"])["token"]

    def test_cli_login_contains_api_url_and_token(self):
        r = self._call()
        body = json.loads(r["body"])
        cli = body["commands"]["cli_login"]
        assert API_URL in cli
        assert body["token"] in cli


class TestListUsers:
    def _call(self, users=None, tenant_exists=True):
        with patch("handlers.admin_users.tenants_repo") as tr, \
             patch("handlers.admin_users.users_repo") as ur:
            tr.get.return_value = _TENANT if tenant_exists else None
            ur.list_by_tenant.return_value = users or []
            return handle_list_users(TENANT, ADMIN)

    def test_unauthorized(self):
        r = handle_list_users(TENANT, "wrong")
        assert r["statusCode"] == 401

    def test_tenant_not_found(self):
        r = self._call(tenant_exists=False)
        assert r["statusCode"] == 404

    def test_returns_users(self):
        r = self._call([_USER])
        users = json.loads(r["body"])["users"]
        assert len(users) == 1
        assert users[0]["user_id"] == USER_ID
        assert users[0]["name"] == "alice"

    def test_no_token_in_response(self):
        user_with_token = {**_USER, "token_hash": "secret_hash"}
        r = self._call([user_with_token])
        body = json.loads(r["body"])
        assert "token_hash" not in body["users"][0]
        assert "token" not in body["users"][0]

    def test_returns_empty(self):
        r = self._call([])
        assert json.loads(r["body"])["users"] == []


class TestDeleteUser:
    def _call(self, user_exists=True):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = _USER if user_exists else None
            return handle_delete_user(TENANT, USER_ID, ADMIN)

    def test_unauthorized(self):
        r = handle_delete_user(TENANT, USER_ID, "wrong")
        assert r["statusCode"] == 401

    def test_user_not_found(self):
        r = self._call(user_exists=False)
        assert r["statusCode"] == 404

    def test_wrong_tenant_returns_404(self):
        wrong_tenant_user = {**_USER, "tenant_id": "other_tenant"}
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = wrong_tenant_user
            r = handle_delete_user(TENANT, USER_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_deletes_user(self):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = _USER
            r = handle_delete_user(TENANT, USER_ID, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["deleted"] is True
        ur.delete.assert_called_once_with(USER_ID)


class TestRotateUserToken:
    def _call(self, user_exists=True):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = _USER if user_exists else None
            return handle_rotate_user_token(TENANT, USER_ID, ADMIN, API_URL)

    def test_unauthorized(self):
        r = handle_rotate_user_token(TENANT, USER_ID, "wrong", API_URL)
        assert r["statusCode"] == 401

    def test_user_not_found(self):
        r = self._call(user_exists=False)
        assert r["statusCode"] == 404

    def test_returns_new_token(self):
        r = self._call()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["token"].startswith("tok_")
        assert "cli_login" in body["commands"]

    def test_updates_token_hash(self):
        with patch("handlers.admin_users.users_repo") as ur, \
             patch("handlers.admin_users.tenants_repo"):
            ur.get.return_value = _USER
            handle_rotate_user_token(TENANT, USER_ID, ADMIN, API_URL)
        ur.update_token_hash.assert_called_once()
