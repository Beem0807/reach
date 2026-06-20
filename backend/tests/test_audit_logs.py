"""Tests for platform and tenant audit log endpoints."""
import json
from unittest.mock import patch

from handlers.audit_logs import (
    handle_list_platform_audit_logs,
    handle_list_tenant_audit_logs,
    platform_audit_logs_handler,
    tenant_audit_logs_handler,
)
from shared.tenant_auth import create_tenant_token

import conftest

ADMIN = conftest.ADMIN_TOKEN

LOG = {
    "log_id":        "log_abc",
    "tenant_id":     "tenant_acme",
    "actor_id":      "user_admin",
    "actor_name":    "Admin",
    "actor_role":    "TENANT_ADMIN",
    "action":        "user.login",
    "resource_type": "user",
    "resource_id":   "user_admin",
    "metadata":      {},
    "ip_address":    "127.0.0.1",
    "created_at":    "2026-06-20T10:00:00",
}

ADMIN_TENANT_TOKEN = create_tenant_token(
    user_id="user_admin",
    tenant_id="tenant_acme",
    role="admin",
    username="admin",
)
USER_TENANT_TOKEN = create_tenant_token(
    user_id="user_bob",
    tenant_id="tenant_acme",
    role="developer",
    username="bob",
)

ACTIVE_TENANT = {"tenant_id": "tenant_acme", "name": "Acme", "status": "ACTIVE"}

# Filter kwargs forwarded to handlers/repos when no query-string filters are set.
NO_FILTERS = dict(action=None, actor=None, resource=None, ip=None, since=None, until=None)


def _tenant_patch():
    """Mock shared.store.tenants_repo so _verify_tenant_payload doesn't hit the DB."""
    return patch("shared.store.tenants_repo")


class TestPlatformAuditLogs:
    def test_authorized_returns_logs(self):
        with patch("handlers.audit_logs.audit_repo") as ar:
            ar.list_platform.return_value = [LOG]
            r = handle_list_platform_audit_logs(ADMIN)
        assert r["statusCode"] == 200
        logs = json.loads(r["body"])["logs"]
        assert len(logs) == 1
        assert logs[0]["action"] == "user.login"

    def test_empty_list(self):
        with patch("handlers.audit_logs.audit_repo") as ar:
            ar.list_platform.return_value = []
            r = handle_list_platform_audit_logs(ADMIN)
        assert json.loads(r["body"])["logs"] == []

    def test_unauthorized_returns_401(self):
        r = handle_list_platform_audit_logs("wrong-token")
        assert r["statusCode"] == 401

    def test_cursor_pagination(self):
        logs = [LOG] * 100
        with patch("handlers.audit_logs.audit_repo") as ar:
            ar.list_platform.return_value = logs
            r = handle_list_platform_audit_logs(ADMIN, limit=100)
        body = json.loads(r["body"])
        # When result count equals limit, next_cursor should be provided
        assert "next_cursor" in body

    def test_no_cursor_when_fewer_than_limit(self):
        with patch("handlers.audit_logs.audit_repo") as ar:
            ar.list_platform.return_value = [LOG]
            r = handle_list_platform_audit_logs(ADMIN, limit=100)
        body = json.loads(r["body"])
        assert "next_cursor" not in body


class TestTenantAuditLogs:
    def test_admin_token_returns_logs(self):
        with _tenant_patch() as tr, patch("handlers.audit_logs.audit_repo") as ar:
            tr.get.return_value = ACTIVE_TENANT
            ar.list_by_tenant.return_value = [LOG]
            r = handle_list_tenant_audit_logs(ADMIN_TENANT_TOKEN)
        assert r["statusCode"] == 200
        assert len(json.loads(r["body"])["logs"]) == 1

    def test_user_token_rejected(self):
        with _tenant_patch() as tr:
            tr.get.return_value = ACTIVE_TENANT
            r = handle_list_tenant_audit_logs(USER_TENANT_TOKEN)
        assert r["statusCode"] == 401

    def test_invalid_token_rejected(self):
        r = handle_list_tenant_audit_logs("not-a-valid-token")
        assert r["statusCode"] == 401

    def test_scoped_to_tenant(self):
        with _tenant_patch() as tr, patch("handlers.audit_logs.audit_repo") as ar:
            tr.get.return_value = ACTIVE_TENANT
            ar.list_by_tenant.return_value = []
            handle_list_tenant_audit_logs(ADMIN_TENANT_TOKEN)
        ar.list_by_tenant.assert_called_once_with("tenant_acme", limit=100, cursor=None, **NO_FILTERS)

    def test_cursor_pagination(self):
        logs = [LOG] * 100
        with _tenant_patch() as tr, patch("handlers.audit_logs.audit_repo") as ar:
            tr.get.return_value = ACTIVE_TENANT
            ar.list_by_tenant.return_value = logs
            r = handle_list_tenant_audit_logs(ADMIN_TENANT_TOKEN, limit=100)
        body = json.loads(r["body"])
        assert "next_cursor" in body


# ---------------------------------------------------------------------------
# Lambda wrappers
# ---------------------------------------------------------------------------

_OK_RESP = {"statusCode": 200, "headers": {}, "body": '{"logs":[]}'}


def _evt(headers=None, qs=None):
    return {
        "headers": headers if headers is not None else {"authorization": f"Bearer {ADMIN}"},
        "queryStringParameters": qs or {},
    }


class TestPlatformAuditLogsHandler:
    def test_missing_auth_returns_401(self):
        r = platform_audit_logs_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delegates_with_defaults(self):
        with patch("handlers.audit_logs.handle_list_platform_audit_logs", return_value=_OK_RESP) as h:
            platform_audit_logs_handler(_evt(), None)
        h.assert_called_once_with(ADMIN, limit=100, cursor=None, **NO_FILTERS)

    def test_delegates_with_limit_and_cursor(self):
        with patch("handlers.audit_logs.handle_list_platform_audit_logs", return_value=_OK_RESP) as h:
            platform_audit_logs_handler(_evt(qs={"limit": "20", "cursor": "2026-01-01"}), None)
        h.assert_called_once_with(ADMIN, limit=20, cursor="2026-01-01", **NO_FILTERS)


class TestTenantAuditLogsHandler:
    def test_missing_auth_returns_401(self):
        r = tenant_audit_logs_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delegates_with_defaults(self):
        with patch("handlers.audit_logs.handle_list_tenant_audit_logs", return_value=_OK_RESP) as h:
            tenant_audit_logs_handler(_evt(headers={"authorization": f"Bearer {ADMIN_TENANT_TOKEN}"}), None)
        h.assert_called_once_with(ADMIN_TENANT_TOKEN, limit=100, cursor=None, **NO_FILTERS)

    def test_delegates_with_limit_and_cursor(self):
        with patch("handlers.audit_logs.handle_list_tenant_audit_logs", return_value=_OK_RESP) as h:
            tenant_audit_logs_handler(
                _evt(
                    headers={"authorization": f"Bearer {ADMIN_TENANT_TOKEN}"},
                    qs={"limit": "50", "cursor": "ts_abc"},
                ),
                None,
            )
        h.assert_called_once_with(ADMIN_TENANT_TOKEN, limit=50, cursor="ts_abc", **NO_FILTERS)
