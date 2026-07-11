"""Tests for platform admin user listing (handlers/admin_users.py)."""
import json
from unittest.mock import patch

import conftest
from handlers.admin_users import handle_list_users, list_users_handler

ADMIN = conftest.ADMIN_TOKEN
TENANT_ID = "tenant_acme"

_TENANT = {"tenant_id": TENANT_ID, "name": "Acme", "status": "ACTIVE"}
_USER = {
    "user_id":    "user_bob",
    "tenant_id":  TENANT_ID,
    "name":       "Bob",
    "username":   "bob",
    "role":       "developer",
    "status":     "ACTIVE",
    "must_reset_password": False,
    "last_login_at": None,
    "created_at": "2026-01-01T00:00:00",
}


class TestHandleListUsers:
    def test_unauthorized_returns_401(self):
        r = handle_list_users(TENANT_ID, "bad-token")
        assert r["statusCode"] == 401

    def test_tenant_not_found_returns_404(self):
        with patch("handlers.admin_users._verify_admin", return_value=True), \
             patch("handlers.admin_users.tenants_repo") as tr:
            tr.get.return_value = None
            r = handle_list_users(TENANT_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_returns_users(self):
        with patch("handlers.admin_users._verify_admin", return_value=True), \
             patch("handlers.admin_users.tenants_repo") as tr, \
             patch("handlers.admin_users.users_repo") as ur:
            tr.get.return_value = _TENANT
            ur.list_by_tenant.return_value = [_USER]
            r = handle_list_users(TENANT_ID, ADMIN)
        assert r["statusCode"] == 200
        users = json.loads(r["body"])["users"]
        assert len(users) == 1
        assert users[0]["username"] == "bob"

    def test_password_hash_not_in_response(self):
        user_with_hash = {**_USER, "password_hash": "secret_hash"}
        with patch("handlers.admin_users._verify_admin", return_value=True), \
             patch("handlers.admin_users.tenants_repo") as tr, \
             patch("handlers.admin_users.users_repo") as ur:
            tr.get.return_value = _TENANT
            ur.list_by_tenant.return_value = [user_with_hash]
            r = handle_list_users(TENANT_ID, ADMIN)
        users = json.loads(r["body"])["users"]
        assert "password_hash" not in users[0]

    def test_empty_user_list(self):
        with patch("handlers.admin_users._verify_admin", return_value=True), \
             patch("handlers.admin_users.tenants_repo") as tr, \
             patch("handlers.admin_users.users_repo") as ur:
            tr.get.return_value = _TENANT
            ur.list_by_tenant.return_value = []
            r = handle_list_users(TENANT_ID, ADMIN)
        assert json.loads(r["body"])["users"] == []

    def _roster(self, n):
        return [{**_USER, "user_id": f"u{i:02d}", "username": f"user{i:02d}", "name": f"User {i:02d}"}
                for i in range(n)]

    def test_pagination_returns_page_and_total(self):
        with patch("handlers.admin_users._verify_admin", return_value=True), \
             patch("handlers.admin_users.tenants_repo") as tr, \
             patch("handlers.admin_users.users_repo") as ur:
            tr.get.return_value = _TENANT
            ur.list_by_tenant.return_value = self._roster(30)
            r = handle_list_users(TENANT_ID, ADMIN, limit=20, offset=0)
        body = json.loads(r["body"])
        assert body["total"] == 30 and len(body["users"]) == 20 and body["offset"] == 0

    def test_q_filters_and_no_page_meta_without_limit(self):
        with patch("handlers.admin_users._verify_admin", return_value=True), \
             patch("handlers.admin_users.tenants_repo") as tr, \
             patch("handlers.admin_users.users_repo") as ur:
            tr.get.return_value = _TENANT
            ur.list_by_tenant.return_value = [{**_USER, "username": "alice"}, {**_USER, "user_id": "u2", "username": "bob"}]
            r = handle_list_users(TENANT_ID, ADMIN, q="ali")
        body = json.loads(r["body"])
        assert [u["username"] for u in body["users"]] == ["alice"] and "total" not in body


class TestListUsersHandler:
    def _evt(self, headers=None, path=None):
        return {
            "headers": headers if headers is not None else {"authorization": f"Bearer {ADMIN}"},
            "pathParameters": path if path is not None else {"tenant_id": TENANT_ID},
            "queryStringParameters": {},
        }

    def test_missing_auth_returns_401(self):
        r = list_users_handler(self._evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delegates_to_handler(self):
        with patch("handlers.admin_users.handle_list_users", return_value={"statusCode": 200, "body": '{"users":[]}'}) as h:
            list_users_handler(self._evt(), None)
        h.assert_called_once_with(TENANT_ID, ADMIN, q=None, limit=None, offset=0)
