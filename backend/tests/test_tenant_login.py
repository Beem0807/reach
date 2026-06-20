"""Tests for tenant login, password change, and /tenant/me."""
import json
from unittest.mock import MagicMock, patch

from handlers.tenant_login import (
    handle_tenant_login,
    handle_change_password,
    handle_tenant_me,
    tenant_login_handler,
    change_password_handler,
    tenant_me_handler,
)
from shared.password import hash_password
from shared.tenant_auth import create_tenant_token

import conftest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
TENANT = {"tenant_id": "tenant_acme", "name": "Acme Corp", "status": "ACTIVE"}
USER = {
    "user_id": "user_123",
    "tenant_id": "tenant_acme",
    "username": "alice",
    "name": "Alice",
    "password_hash": hash_password("secret123"),
    "role": "admin",
    "status": "ACTIVE",
    "must_reset_password": False,
    "disabled_at": None,
}


def _login(tenant_name="Acme Corp", username="alice", password="secret123", tenant=None, user=None):
    resolved = (TENANT if tenant is None else tenant) if tenant_name == (tenant or TENANT).get("name") else None
    user = user if user is not None else USER
    with patch("handlers.tenant_login.tenants_repo") as tr, \
         patch("handlers.tenant_login.users_repo") as ur, \
         patch("handlers.tenant_login.audit"):
        tr.get_by_name.return_value = resolved
        tr.get.return_value = None
        ur.get_by_username.return_value = user
        ur.set_last_login.return_value = None
        return handle_tenant_login({"tenant_name": tenant_name, "username": username, "password": password})


# ---------------------------------------------------------------------------
# handle_tenant_login
# ---------------------------------------------------------------------------
class TestTenantLogin:
    def test_success_returns_token_and_user(self):
        r = _login()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert "token" in body
        assert body["user"]["username"] == "alice"
        assert body["user"]["role"] == "admin"
        assert body["must_reset_password"] is False

    def test_must_reset_flag_propagated(self):
        user = {**USER, "must_reset_password": True}
        r = _login(user=user)
        assert json.loads(r["body"])["must_reset_password"] is True

    def test_wrong_password_returns_401(self):
        r = _login(password="wrongpassword")
        assert r["statusCode"] == 401

    def test_tenant_not_found_returns_401(self):
        r = _login(tenant_name="Unknown Tenant")
        assert r["statusCode"] == 401

    def test_disabled_tenant_returns_403(self):
        r = _login(tenant={**TENANT, "status": "DISABLED"})
        assert r["statusCode"] == 403

    def test_disabled_user_returns_403(self):
        user = {**USER, "disabled_at": "2026-01-01T00:00:00"}
        r = _login(user=user)
        assert r["statusCode"] == 403

    def test_revoked_user_returns_403(self):
        user = {**USER, "status": "REVOKED"}
        r = _login(user=user)
        assert r["statusCode"] == 403

    def test_user_without_password_hash_returns_401(self):
        user = {**USER, "password_hash": None}
        r = _login(user=user)
        assert r["statusCode"] == 401

    def test_missing_fields_returns_400(self):
        with patch("handlers.tenant_login.tenants_repo"):
            r = handle_tenant_login({"tenant_name": "Acme Corp"})
        assert r["statusCode"] == 400

    def test_user_not_found_returns_401(self):
        with patch("handlers.tenant_login.tenants_repo") as tr, \
             patch("handlers.tenant_login.users_repo") as ur, \
             patch("handlers.tenant_login.audit"):
            tr.get_by_name.return_value = TENANT
            tr.get.return_value = None
            ur.get_by_username.return_value = None
            r = handle_tenant_login({"tenant_name": "Acme Corp", "username": "nobody", "password": "x"})
        assert r["statusCode"] == 401


class TestTenantLoginFailureAudit:
    """Failed logins are audited as user.login_failed for compliance."""

    def _login_capturing_audit(self, *, tenant_name="Acme Corp", username="alice",
                               password="secret123", tenant=None, user=USER):
        resolved = (TENANT if tenant is None else tenant) if tenant_name == (tenant or TENANT).get("name") else None
        with patch("handlers.tenant_login.tenants_repo") as tr, \
             patch("handlers.tenant_login.users_repo") as ur, \
             patch("handlers.tenant_login.audit") as mock_audit:
            tr.get_by_name.return_value = resolved
            tr.get.return_value = None
            ur.get_by_username.return_value = user
            ur.set_last_login.return_value = None
            handle_tenant_login(
                {"tenant_name": tenant_name, "username": username, "password": password},
                ip="203.0.113.7",
            )
        return mock_audit

    def _assert_failed(self, mock_audit, reason):
        mock_audit.write.assert_called_once()
        args, kwargs = mock_audit.write.call_args
        assert args[0] == "user.login_failed"
        assert kwargs["metadata"]["reason"] == reason
        assert kwargs["ip_address"] == "203.0.113.7"

    def test_bad_password_audited(self):
        self._assert_failed(self._login_capturing_audit(password="wrong"), "bad_password")

    def test_tenant_not_found_audited(self):
        self._assert_failed(self._login_capturing_audit(tenant_name="Nope"), "tenant_not_found")

    def test_tenant_disabled_audited(self):
        self._assert_failed(
            self._login_capturing_audit(tenant={**TENANT, "status": "DISABLED"}), "tenant_disabled")

    def test_user_not_found_audited(self):
        self._assert_failed(self._login_capturing_audit(user=None), "user_not_found")

    def test_account_disabled_audited(self):
        mock_audit = self._login_capturing_audit(user={**USER, "status": "REVOKED"})
        self._assert_failed(mock_audit, "account_disabled")
        # the known user_id is recorded as the resource even though the actor is unknown
        assert mock_audit.write.call_args.kwargs["resource_id"] == "user_123"

    def test_successful_login_does_not_write_failed(self):
        with patch("handlers.tenant_login.tenants_repo") as tr, \
             patch("handlers.tenant_login.users_repo") as ur, \
             patch("handlers.tenant_login.audit") as mock_audit:
            tr.get_by_name.return_value = TENANT
            tr.get.return_value = None
            ur.get_by_username.return_value = USER
            handle_tenant_login({"tenant_name": "Acme Corp", "username": "alice", "password": "secret123"})
        actions = [c.args[0] for c in mock_audit.write.call_args_list]
        assert "user.login" in actions
        assert "user.login_failed" not in actions


# ---------------------------------------------------------------------------
# handle_change_password
# ---------------------------------------------------------------------------
class TestChangePassword:
    _payload = {"sub": "user_123", "tenant_id": "tenant_acme", "role": "admin"}

    def test_success(self):
        with patch("handlers.tenant_login.users_repo") as ur, \
             patch("handlers.tenant_login.audit"):
            ur.get.return_value = USER
            ur.update_password.return_value = None
            r = handle_change_password(
                {"current_password": "secret123", "new_password": "newpass99"},
                self._payload,
            )
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["changed"] is True

    def test_wrong_current_password(self):
        with patch("handlers.tenant_login.users_repo") as ur:
            ur.get.return_value = USER
            r = handle_change_password(
                {"current_password": "bad", "new_password": "newpass99"},
                self._payload,
            )
        assert r["statusCode"] == 401

    def test_new_password_too_short(self):
        with patch("handlers.tenant_login.users_repo") as ur:
            ur.get.return_value = USER
            r = handle_change_password(
                {"current_password": "secret123", "new_password": "short"},
                self._payload,
            )
        assert r["statusCode"] == 400

    def test_missing_fields(self):
        r = handle_change_password({}, self._payload)
        assert r["statusCode"] == 400

    def test_user_not_found(self):
        with patch("handlers.tenant_login.users_repo") as ur:
            ur.get.return_value = None
            r = handle_change_password(
                {"current_password": "secret123", "new_password": "newpass99"},
                self._payload,
            )
        assert r["statusCode"] == 404


# ---------------------------------------------------------------------------
# handle_tenant_me
# ---------------------------------------------------------------------------
class TestTenantMe:
    _payload = {"sub": "user_123", "tenant_id": "tenant_acme"}

    def test_returns_user_and_tenant(self):
        with patch("handlers.tenant_login.users_repo") as ur, \
             patch("handlers.tenant_login.tenants_repo") as tr:
            ur.get.return_value = USER
            tr.get.return_value = TENANT
            r = handle_tenant_me(self._payload)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["username"] == "alice"
        assert body["tenant_name"] == "Acme Corp"

    def test_user_not_found_returns_404(self):
        with patch("handlers.tenant_login.users_repo") as ur:
            ur.get.return_value = None
            r = handle_tenant_me(self._payload)
        assert r["statusCode"] == 404


# ---------------------------------------------------------------------------
# Lambda handler wrappers
# ---------------------------------------------------------------------------

_VALID_TOKEN = create_tenant_token(
    user_id="user_123",
    tenant_id="tenant_acme",
    role="admin",
    username="alice",
)

_OK = {"statusCode": 200, "headers": {}, "body": "{}"}


def _evt(headers=None, body=None, path=None, qs=None):
    return {
        "headers": headers if headers is not None else {"authorization": f"Bearer {_VALID_TOKEN}"},
        "body": body,
        "pathParameters": path or {},
        "queryStringParameters": qs or {},
    }


class TestTenantLoginHandler:
    def test_invalid_json_returns_400(self):
        r = tenant_login_handler(_evt(body="not-json"), None)
        assert r["statusCode"] == 400

    def test_delegates_to_handler(self):
        body = {"tenant_name": "Acme", "username": "alice", "password": "pw"}
        with patch("handlers.tenant_login.handle_tenant_login", return_value=_OK) as h:
            tenant_login_handler(_evt(body=json.dumps(body)), None)
        h.assert_called_once()
        called_body = h.call_args[0][0]
        assert called_body["username"] == "alice"

    def test_none_body_treated_as_empty_dict(self):
        with patch("handlers.tenant_login.handle_tenant_login", return_value=_OK) as h:
            tenant_login_handler(_evt(body=None), None)
        h.assert_called_once()
        called_body = h.call_args[0][0]
        assert called_body == {}


class TestChangePasswordHandler:
    def test_missing_auth_returns_401(self):
        r = change_password_handler(_evt(headers={}, body="{}"), None)
        assert r["statusCode"] == 401

    def test_invalid_token_returns_401(self):
        r = change_password_handler(
            _evt(headers={"authorization": "Bearer bad-token"}, body="{}"),
            None,
        )
        assert r["statusCode"] == 401

    def test_invalid_json_returns_400(self):
        with patch("handlers.tenant_login._verify_tenant_payload", return_value={"sub": "u", "tenant_id": "t"}):
            r = change_password_handler(_evt(body="not-json"), None)
        assert r["statusCode"] == 400

    def test_delegates_to_handler(self):
        payload = {"sub": "user_123", "tenant_id": "tenant_acme", "role": "admin"}
        body = {"current_password": "old", "new_password": "newpass99"}
        with patch("handlers.tenant_login._verify_tenant_payload", return_value=payload), \
             patch("handlers.tenant_login.handle_change_password", return_value=_OK) as h:
            change_password_handler(_evt(body=json.dumps(body)), None)
        h.assert_called_once()


class TestTenantMeHandler:
    def test_missing_auth_returns_401(self):
        r = tenant_me_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_invalid_token_returns_401(self):
        r = tenant_me_handler(
            _evt(headers={"authorization": "Bearer bad-token"}),
            None,
        )
        assert r["statusCode"] == 401

    def test_delegates_to_handler(self):
        payload = {"sub": "user_123", "tenant_id": "tenant_acme", "role": "admin"}
        with patch("handlers.tenant_login._verify_tenant_payload", return_value=payload), \
             patch("handlers.tenant_login.handle_tenant_me", return_value=_OK) as h:
            tenant_me_handler(_evt(), None)
        h.assert_called_once_with(payload)
