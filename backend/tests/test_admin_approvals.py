"""Tests for admin_approvals handler: duration parsing, list, review, and Lambda wrappers."""
import json
from unittest.mock import patch, MagicMock

import pytest

from handlers.admin_approvals import (
    _parse_expires_at,
    handle_delete_approval,
    handle_list_approvals,
    handle_pre_approve_command,
    handle_review_approval,
    delete_approval_handler,
    list_approvals_handler,
    pre_approve_command_handler,
    review_approval_handler,
)

ADMIN = "test-admin-token"
APPROVAL_ID = "appr_abc123"

_APPROVAL = {
    "approval_id": APPROVAL_ID,
    "tenant_id": "tenant_1",
    "agent_id": "agent_a",
    "command": "docker ps",
    "requested_by": "user_1",
    "status": "pending",
    "created_at": "2026-01-01T00:00:00+00:00",
    "reviewed_at": None,
    "reviewed_by": None,
    "expires_at": None,
}

_BEARER = {"authorization": f"Bearer {ADMIN}"}



# ---------------------------------------------------------------------------
# _parse_expires_at
# ---------------------------------------------------------------------------

class TestParseExpiresAt:
    def test_permanent_returns_none(self):
        ok, exp = _parse_expires_at("permanent")
        assert ok is True
        assert exp is None

    def test_empty_string_returns_none(self):
        ok, exp = _parse_expires_at("")
        assert ok is True
        assert exp is None

    def test_none_returns_none(self):
        ok, exp = _parse_expires_at(None)
        assert ok is True
        assert exp is None

    @pytest.mark.parametrize("duration,expected_seconds", [
        ("1h", 3600),
        ("8h", 28800),
        ("24h", 86400),
        ("7d", 604800),
    ])
    def test_known_durations_return_future_timestamp(self, duration, expected_seconds):
        from datetime import datetime, timezone
        before = datetime.now(tz=timezone.utc)
        ok, exp = _parse_expires_at(duration)
        after = datetime.now(tz=timezone.utc)
        assert ok is True
        assert exp is not None
        parsed = datetime.fromisoformat(exp)
        delta = (parsed - before).total_seconds()
        assert expected_seconds - 2 <= delta <= expected_seconds + 2

    def test_custom_hours(self):
        from datetime import datetime, timezone
        before = datetime.now(tz=timezone.utc)
        ok, exp = _parse_expires_at("3h")
        assert ok is True
        parsed = datetime.fromisoformat(exp)
        delta = (parsed - before).total_seconds()
        assert 3 * 3600 - 2 <= delta <= 3 * 3600 + 2

    def test_custom_days(self):
        from datetime import datetime, timezone
        before = datetime.now(tz=timezone.utc)
        ok, exp = _parse_expires_at("30d")
        assert ok is True
        parsed = datetime.fromisoformat(exp)
        delta = (parsed - before).total_seconds()
        assert 30 * 86400 - 2 <= delta <= 30 * 86400 + 2

    def test_invalid_format_returns_false(self):
        ok, exp = _parse_expires_at("2weeks")
        assert ok is False
        assert exp is None

    def test_invalid_unit_returns_false(self):
        ok, exp = _parse_expires_at("5m")
        assert ok is False

    def test_bare_number_returns_false(self):
        ok, exp = _parse_expires_at("100")
        assert ok is False


# ---------------------------------------------------------------------------
# handle_list_approvals
# ---------------------------------------------------------------------------

class TestHandleListApprovals:
    def _call(self, query=None, token=ADMIN, approvals=None):
        with patch("handlers.admin_approvals.approvals_repo") as ar:
            ar.list_by_tenant.return_value = approvals or []
            ar.list_by_agent.return_value = approvals or []
            return handle_list_approvals(query or {}, token), ar

    def test_unauthorized(self):
        r, _ = self._call(token="wrong")
        assert r["statusCode"] == 401

    def test_requires_tenant_or_agent_id(self):
        r, ar = self._call(query={})
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["approvals"] == []
        ar.list_by_tenant.assert_not_called()
        ar.list_by_agent.assert_not_called()

    def test_list_by_tenant(self):
        r, ar = self._call(query={"tenant_id": "tenant_1"}, approvals=[_APPROVAL])
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert len(body["approvals"]) == 1
        ar.list_by_tenant.assert_called_once_with("tenant_1", agent_id=None, status=None, limit=20, cursor=None)

    def test_list_by_agent(self):
        r, ar = self._call(query={"agent_id": "agent_a"}, approvals=[_APPROVAL])
        assert r["statusCode"] == 200
        ar.list_by_agent.assert_called_once_with("agent_a", status=None, limit=20, cursor=None)

    def test_status_filter_passed_through(self):
        r, ar = self._call(query={"tenant_id": "tenant_1", "status": "pending"})
        ar.list_by_tenant.assert_called_once_with("tenant_1", agent_id=None, status="pending", limit=20, cursor=None)

    def test_agent_id_filter_passed_through(self):
        r, ar = self._call(query={"tenant_id": "tenant_1", "agent_id": "agent_a"})
        ar.list_by_tenant.assert_called_once_with("tenant_1", agent_id="agent_a", status=None, limit=20, cursor=None)

    # --- pagination ---

    def test_no_next_cursor_when_fewer_than_limit(self):
        r, _ = self._call(query={"tenant_id": "tenant_1"}, approvals=[_APPROVAL])
        body = json.loads(r["body"])
        assert "next_cursor" not in body

    def test_next_cursor_present_when_full_page(self):
        page = [dict(_APPROVAL, created_at=f"2026-01-{i:02d}T00:00:00+00:00") for i in range(1, 21)]
        r, _ = self._call(query={"tenant_id": "tenant_1", "limit": "20"}, approvals=page)
        body = json.loads(r["body"])
        assert "next_cursor" in body

    def test_cursor_decoded_and_passed_to_repo(self):
        import base64
        encoded = base64.urlsafe_b64encode(b"2026-01-10T00:00:00+00:00").decode()
        r, ar = self._call(query={"tenant_id": "tenant_1", "cursor": encoded})
        _, kwargs = ar.list_by_tenant.call_args
        assert kwargs["cursor"] == "2026-01-10T00:00:00+00:00"

    def test_limit_clamped_to_100(self):
        r, ar = self._call(query={"tenant_id": "tenant_1", "limit": "999"})
        _, kwargs = ar.list_by_tenant.call_args
        assert kwargs["limit"] == 100

    def test_invalid_limit_defaults_to_20(self):
        r, ar = self._call(query={"tenant_id": "tenant_1", "limit": "bad"})
        _, kwargs = ar.list_by_tenant.call_args
        assert kwargs["limit"] == 20


# ---------------------------------------------------------------------------
# handle_review_approval
# ---------------------------------------------------------------------------

class TestHandleReviewApproval:
    def _call(self, action="approve", body=None, token=ADMIN, approval=_APPROVAL):
        with patch("handlers.admin_approvals.approvals_repo") as ar:
            ar.get.return_value = approval
            return handle_review_approval(APPROVAL_ID, action, token, body), ar

    def test_unauthorized(self):
        r, _ = self._call(token="wrong")
        assert r["statusCode"] == 401

    def test_invalid_action(self):
        r, _ = self._call(action="revoke")
        assert r["statusCode"] == 400

    def test_approval_not_found(self):
        with patch("handlers.admin_approvals.approvals_repo") as ar:
            ar.get.return_value = None
            r = handle_review_approval(APPROVAL_ID, "approve", ADMIN)
        assert r["statusCode"] == 404

    def test_approve_sets_approved_status(self):
        r, ar = self._call(action="approve")
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["status"] == "approved"
        call_args = ar.update_status.call_args
        assert call_args[0][1] == "approved"

    def test_deny_sets_denied_status(self):
        r, ar = self._call(action="deny")
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["status"] == "denied"
        call_args = ar.update_status.call_args
        assert call_args[0][1] == "denied"

    def test_approve_permanent_by_default(self):
        r, ar = self._call(action="approve", body={})
        assert r["statusCode"] == 200
        kwargs = ar.update_status.call_args[1]
        assert kwargs.get("expires_at") is None

    def test_approve_with_duration_sets_expires_at(self):
        r, ar = self._call(action="approve", body={"duration": "1h"})
        assert r["statusCode"] == 200
        kwargs = ar.update_status.call_args[1]
        assert kwargs.get("expires_at") is not None

    def test_approve_with_invalid_duration_returns_400(self):
        r, ar = self._call(action="approve", body={"duration": "bad"})
        assert r["statusCode"] == 400

    def test_deny_ignores_duration(self):
        r, ar = self._call(action="deny", body={"duration": "1h"})
        assert r["statusCode"] == 200
        kwargs = ar.update_status.call_args[1]
        assert kwargs.get("expires_at") is None

    def test_response_includes_reviewed_at(self):
        r, _ = self._call(action="approve")
        body = json.loads(r["body"])
        assert body.get("reviewed_at") is not None

    def test_response_includes_reviewed_by_admin(self):
        r, _ = self._call(action="approve")
        body = json.loads(r["body"])
        assert body.get("reviewed_by") == "admin"

    # --- re-approve / revoke / update-duration ---

    def test_reapprove_already_approved_with_new_duration(self):
        already_approved = {**_APPROVAL, "status": "approved", "expires_at": "2099-01-01T00:00:00+00:00"}
        r, ar = self._call(action="approve", body={"duration": "24h"}, approval=already_approved)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["status"] == "approved"
        kwargs = ar.update_status.call_args[1]
        assert kwargs.get("expires_at") is not None

    def test_reapprove_as_permanent_clears_expiry(self):
        already_approved = {**_APPROVAL, "status": "approved", "expires_at": "2099-01-01T00:00:00+00:00"}
        r, ar = self._call(action="approve", body={}, approval=already_approved)
        assert r["statusCode"] == 200
        kwargs = ar.update_status.call_args[1]
        assert kwargs.get("expires_at") is None

    def test_revoke_approved_record(self):
        already_approved = {**_APPROVAL, "status": "approved"}
        r, ar = self._call(action="deny", approval=already_approved)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["status"] == "denied"
        assert ar.update_status.call_args[0][1] == "denied"

    def test_reapprove_denied_record(self):
        denied = {**_APPROVAL, "status": "denied"}
        r, ar = self._call(action="approve", body={"duration": "8h"}, approval=denied)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["status"] == "approved"
        kwargs = ar.update_status.call_args[1]
        assert kwargs.get("expires_at") is not None


# ---------------------------------------------------------------------------
# Lambda handler wrappers
# ---------------------------------------------------------------------------

_OK = {"statusCode": 200, "headers": {}, "body": '{"ok": true}'}


def _evt(headers=None, body=None, path=None, qs=None):
    return {
        "headers": _BEARER if headers is None else headers,
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": qs or {},
    }


class TestListApprovalsHandler:
    def test_delegates(self):
        with patch("handlers.admin_approvals.handle_list_approvals", return_value=_OK) as h:
            r = list_approvals_handler(_evt(qs={"tenant_id": "t1"}), None)
        h.assert_called_once_with({"tenant_id": "t1"}, ADMIN)
        assert r == _OK

    def test_missing_auth_returns_401(self):
        r = list_approvals_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401


class TestReviewApprovalHandler:
    def test_approve_delegates(self):
        with patch("handlers.admin_approvals.handle_review_approval", return_value=_OK) as h:
            r = review_approval_handler(
                _evt(body={"duration": "1h"}, path={"approval_id": APPROVAL_ID, "action": "approve"}),
                None,
            )
        h.assert_called_once_with(APPROVAL_ID, "approve", ADMIN, {"duration": "1h"})
        assert r == _OK

    def test_deny_delegates(self):
        with patch("handlers.admin_approvals.handle_review_approval", return_value=_OK) as h:
            review_approval_handler(
                _evt(path={"approval_id": APPROVAL_ID, "action": "deny"}),
                None,
            )
        h.assert_called_once_with(APPROVAL_ID, "deny", ADMIN, {})

    def test_missing_auth_returns_401(self):
        r = review_approval_handler(
            _evt(headers={}, path={"approval_id": APPROVAL_ID, "action": "approve"}),
            None,
        )
        assert r["statusCode"] == 401

    def test_invalid_json_body_uses_empty_dict(self):
        with patch("handlers.admin_approvals.handle_review_approval", return_value=_OK) as h:
            evt = _evt(path={"approval_id": APPROVAL_ID, "action": "approve"})
            evt["body"] = "not-json"
            review_approval_handler(evt, None)
        h.assert_called_once_with(APPROVAL_ID, "approve", ADMIN, {})


class TestHandlePreApproveCommand:
    _AGENT = {"agent_id": "agent_a", "tenant_id": "tenant_1"}

    def _call(self, body=None, token=ADMIN, agent=_AGENT, existing_approved=None):
        with patch("handlers.admin_approvals.agents_repo") as agr, \
             patch("handlers.admin_approvals.approvals_repo") as ar:
            agr.get.return_value = agent
            ar.list_by_agent.return_value = existing_approved if existing_approved is not None else []
            return handle_pre_approve_command(body or {"agent_id": "agent_a", "command": "docker ps"}, token), ar

    def test_unauthorized(self):
        r, _ = self._call(token="wrong")
        assert r["statusCode"] == 401

    def test_missing_agent_id(self):
        r, _ = self._call(body={"command": "docker ps"})
        assert r["statusCode"] == 400

    def test_missing_command(self):
        r, _ = self._call(body={"agent_id": "agent_a"})
        assert r["statusCode"] == 400

    def test_agent_not_found(self):
        with patch("handlers.admin_approvals.agents_repo") as agr, \
             patch("handlers.admin_approvals.approvals_repo"):
            agr.get.return_value = None
            r = handle_pre_approve_command({"agent_id": "agent_a", "command": "docker ps"}, ADMIN)
        assert r["statusCode"] == 404

    def test_creates_approved_record(self):
        r, ar = self._call()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["status"] == "approved"
        assert body["command"] == "docker ps"
        assert body["agent_id"] == "agent_a"
        assert body["tenant_id"] == "tenant_1"
        assert body["reviewed_by"] == "admin"
        assert body["approval_id"].startswith("appr_")
        ar.create.assert_called_once()

    def test_permanent_by_default(self):
        r, _ = self._call()
        body = json.loads(r["body"])
        assert body["expires_at"] is None

    def test_with_duration(self):
        r, _ = self._call(body={"agent_id": "agent_a", "command": "docker ps", "duration": "8h"})
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["expires_at"] is not None

    def test_invalid_duration_returns_400(self):
        r, _ = self._call(body={"agent_id": "agent_a", "command": "docker ps", "duration": "bad"})
        assert r["statusCode"] == 400

    def test_duplicate_active_approval_returns_409(self):
        existing = {**_APPROVAL, "command": "docker ps", "status": "approved"}
        r, ar = self._call(existing_approved=[existing])
        assert r["statusCode"] == 409
        ar.create.assert_not_called()

    def test_no_conflict_when_existing_is_different_command(self):
        existing = {**_APPROVAL, "command": "docker restart app", "status": "approved"}
        r, ar = self._call(existing_approved=[existing])
        assert r["statusCode"] == 200
        ar.create.assert_called_once()

    # --- bulk (commands list) ---

    def test_bulk_creates_all_commands(self):
        r, ar = self._call(body={"agent_id": "agent_a", "commands": ["docker ps", "docker logs app"]})
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert len(body["created"]) == 2
        assert body["skipped"] == []
        assert ar.create.call_count == 2

    def test_bulk_skips_existing_without_error(self):
        existing = {**_APPROVAL, "command": "docker ps", "status": "approved"}
        r, _ = self._call(
            body={"agent_id": "agent_a", "commands": ["docker ps", "docker logs app"]},
            existing_approved=[existing],
        )
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert len(body["created"]) == 1
        assert body["created"][0]["command"] == "docker logs app"
        assert len(body["skipped"]) == 1
        assert body["skipped"][0]["command"] == "docker ps"
        assert body["skipped"][0]["reason"] == "already_approved"

    def test_bulk_all_skipped_is_still_200(self):
        existing = {**_APPROVAL, "command": "docker ps", "status": "approved"}
        r, ar = self._call(
            body={"agent_id": "agent_a", "commands": ["docker ps"]},
            existing_approved=[existing],
        )
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["created"] == []
        assert len(body["skipped"]) == 1
        ar.create.assert_not_called()

    def test_bulk_empty_list_returns_400(self):
        r, _ = self._call(body={"agent_id": "agent_a", "commands": []})
        assert r["statusCode"] == 400

    def test_bulk_with_duration(self):
        r, _ = self._call(body={"agent_id": "agent_a", "commands": ["docker ps"], "duration": "8h"})
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["created"][0]["expires_at"] is not None

    def test_single_command_returns_flat_object_not_list(self):
        r, _ = self._call()
        body = json.loads(r["body"])
        assert "command" in body
        assert "created" not in body


class TestPreApproveCommandHandler:
    def test_delegates(self):
        with patch("handlers.admin_approvals.handle_pre_approve_command", return_value=_OK) as h:
            r = pre_approve_command_handler(
                _evt(body={"agent_id": "agent_a", "command": "docker ps"}),
                None,
            )
        h.assert_called_once_with({"agent_id": "agent_a", "command": "docker ps"}, ADMIN)
        assert r == _OK

    def test_missing_auth_returns_401(self):
        r = pre_approve_command_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_invalid_json_body_uses_empty_dict(self):
        with patch("handlers.admin_approvals.handle_pre_approve_command", return_value=_OK) as h:
            evt = _evt()
            evt["body"] = "not-json"
            pre_approve_command_handler(evt, None)
        h.assert_called_once_with({}, ADMIN)


class TestHandleDeleteApproval:
    def _call(self, token=ADMIN, approval=_APPROVAL):
        with patch("handlers.admin_approvals.approvals_repo") as ar:
            ar.get.return_value = approval
            return handle_delete_approval(APPROVAL_ID, token), ar

    def test_unauthorized(self):
        r, _ = self._call(token="wrong")
        assert r["statusCode"] == 401

    def test_not_found(self):
        with patch("handlers.admin_approvals.approvals_repo") as ar:
            ar.get.return_value = None
            r = handle_delete_approval(APPROVAL_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_deletes_and_returns_ok(self):
        r, ar = self._call()
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["deleted"] is True
        ar.delete.assert_called_once_with(APPROVAL_ID)

    def test_works_on_any_status(self):
        for status in ("pending", "approved", "denied"):
            r, ar = self._call(approval={**_APPROVAL, "status": status})
            assert r["statusCode"] == 200
            ar.delete.assert_called_once_with(APPROVAL_ID)


class TestDeleteApprovalHandler:
    def test_delegates(self):
        with patch("handlers.admin_approvals.handle_delete_approval", return_value=_OK) as h:
            r = delete_approval_handler(_evt(path={"approval_id": APPROVAL_ID}), None)
        h.assert_called_once_with(APPROVAL_ID, ADMIN)
        assert r == _OK

    def test_missing_auth_returns_401(self):
        r = delete_approval_handler(_evt(headers={}, path={"approval_id": APPROVAL_ID}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# DynamoDB update_status - expires_at clearing
# ---------------------------------------------------------------------------

class TestDynamoUpdateStatusExpiresAt:
    """Verify that update_status with expires_at=None sends REMOVE expires_at
    so that re-approving as permanent actually clears any previous expiry."""

    @staticmethod
    def _dynamo_mod():
        import sys
        # boto3.resource('dynamodb') is called at module level; patch it so the
        # module can be imported in a test environment without real AWS credentials.
        if "shared.repos.dynamo" not in sys.modules:
            with patch("boto3.resource"):
                import shared.repos.dynamo  # noqa: F401
        import shared.repos.dynamo as m
        return m

    def test_with_expires_at_sets_value(self):
        dynamo = self._dynamo_mod()
        table_mock = MagicMock()
        with patch.object(dynamo, "_TABLE_APPROVALS", table_mock):
            dynamo.ApprovalRepo().update_status(
                "appr_1", "approved", "2026-01-01T00:00:00+00:00", "admin",
                expires_at="2026-02-01T00:00:00+00:00",
            )
        call_kwargs = table_mock.update_item.call_args[1]
        expr = call_kwargs["UpdateExpression"]
        assert "expires_at" in expr
        assert "REMOVE" not in expr
        assert ":exp" in call_kwargs["ExpressionAttributeValues"]

    def test_without_expires_at_removes_field(self):
        dynamo = self._dynamo_mod()
        table_mock = MagicMock()
        with patch.object(dynamo, "_TABLE_APPROVALS", table_mock):
            dynamo.ApprovalRepo().update_status(
                "appr_1", "approved", "2026-01-01T00:00:00+00:00", "admin",
                expires_at=None,
            )
        call_kwargs = table_mock.update_item.call_args[1]
        expr = call_kwargs["UpdateExpression"]
        assert "REMOVE expires_at" in expr
        assert ":exp" not in call_kwargs.get("ExpressionAttributeValues", {})
