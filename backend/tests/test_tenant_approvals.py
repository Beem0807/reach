"""Tests for tenant_approvals handler: list_my_pending, list_agent_approved, and Lambda wrappers."""
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from handlers.tenant_approvals import (
    handle_list_my_pending,
    handle_list_agent_approved,
    list_my_pending_handler,
    list_agent_approved_handler,
)

AGENT_ID = "agent_a"
USER_ID = "user_1"
TENANT_ID = "tenant_1"

_USER = {"user_id": USER_ID, "tenant_id": TENANT_ID, "name": "Alice"}
_AGENT = {"agent_id": AGENT_ID, "tenant_id": TENANT_ID}

_APPROVED = {
    "approval_id": "appr_1",
    "agent_id": AGENT_ID,
    "command": "docker ps",
    "status": "approved",
    "requested_by": USER_ID,
    "expires_at": None,
}
_PENDING = {**_APPROVED, "approval_id": "appr_2", "status": "pending"}
_DENIED = {**_APPROVED, "approval_id": "appr_3", "status": "denied"}
_EXPIRED = {
    **_APPROVED,
    "approval_id": "appr_4",
    "status": "expired",
    "expires_at": "2020-01-01T00:00:00+00:00",
}


def _patch(agent=_AGENT, user=_USER):
    return lambda f: f


# ---------------------------------------------------------------------------
# handle_list_my_pending
# ---------------------------------------------------------------------------

class TestHandleListMyPending:
    def _call(self, query=None, user=_USER, approvals=None, agent=_AGENT):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            agr.get.return_value = agent
            ar.list_by_tenant.return_value = approvals or [_PENDING]
            return handle_list_my_pending(query or {}, "tok"), ar

    def test_unauthorized(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=None):
            r = handle_list_my_pending({}, "bad")
        assert r["statusCode"] == 401

    def test_returns_pending_items(self):
        r, _ = self._call()
        body = json.loads(r["body"])
        assert len(body["approvals"]) == 1
        assert body["approvals"][0]["status"] == "pending"

    def test_filters_by_requested_by(self):
        r, ar = self._call()
        ar.list_by_tenant.assert_called_once_with(
            TENANT_ID,
            agent_id=None,
            status="pending",
            requested_by=USER_ID,
        )

    def test_agent_id_filter_passed_through(self):
        r, ar = self._call(query={"agent_id": AGENT_ID})
        ar.list_by_tenant.assert_called_once_with(
            TENANT_ID,
            agent_id=AGENT_ID,
            status="pending",
            requested_by=USER_ID,
        )

    def test_agent_id_not_found_returns_404(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_USER), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo"):
            agr.get.return_value = None
            r = handle_list_my_pending({"agent_id": AGENT_ID}, "tok")
        assert r["statusCode"] == 404

    def test_agent_id_no_access_returns_404(self):
        no_access_user = {**_USER, "allowed_agent_ids": ["agent_other"]}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=no_access_user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo"):
            agr.get.return_value = _AGENT
            r = handle_list_my_pending({"agent_id": AGENT_ID}, "tok")
        assert r["statusCode"] == 404

    def test_no_agent_filter_excludes_inaccessible_agents(self):
        restricted_user = {**_USER, "allowed_agent_ids": ["agent_other"]}
        pending_on_other = {**_PENDING, "agent_id": "agent_other"}
        pending_on_a = {**_PENDING, "agent_id": AGENT_ID}
        def fake_get(aid):
            return {"agent_id": aid, "tenant_id": TENANT_ID}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=restricted_user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            agr.get.side_effect = fake_get
            ar.list_by_tenant.return_value = [pending_on_a, pending_on_other]
            r = handle_list_my_pending({}, "tok")
        body = json.loads(r["body"])
        ids = [a["agent_id"] for a in body["approvals"]]
        assert "agent_other" in ids
        assert AGENT_ID not in ids

    def test_no_agent_filter_unrestricted_user_sees_all(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_USER), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            agr.get.return_value = _AGENT
            ar.list_by_tenant.return_value = [_PENDING]
            r = handle_list_my_pending({}, "tok")
        body = json.loads(r["body"])
        assert len(body["approvals"]) == 1


# ---------------------------------------------------------------------------
# handle_list_agent_approved
# ---------------------------------------------------------------------------

class TestHandleListAgentApproved:
    def _call(self, status="approved", user=_USER, agent=_AGENT, items=None):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.can_access_agent", return_value=True), \
             patch("handlers.tenant_approvals.approvals_repo") as apr:
            agr.get.return_value = agent
            apr.list_by_agent.return_value = items if items is not None else [_APPROVED]
            return handle_list_agent_approved(AGENT_ID, "tok", status=status), apr

    def test_unauthorized(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=None):
            r = handle_list_agent_approved(AGENT_ID, "bad")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_USER), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.can_access_agent", return_value=True):
            agr.get.return_value = None
            r = handle_list_agent_approved(AGENT_ID, "tok")
        assert r["statusCode"] == 404

    def test_agent_access_denied(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_USER), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.can_access_agent", return_value=False):
            agr.get.return_value = _AGENT
            r = handle_list_agent_approved(AGENT_ID, "tok")
        assert r["statusCode"] == 404

    def test_invalid_status_returns_400(self):
        r, _ = self._call(status="unknown")
        assert r["statusCode"] == 400

    # --- approved ---

    def test_approved_returns_effective_list(self):
        r, apr = self._call(status="approved", items=[_APPROVED])
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert "docker ps" in body["approved_commands"]
        apr.list_by_agent.assert_called_once_with(AGENT_ID, status="approved")

    def test_approved_includes_approvals_detail(self):
        r, _ = self._call(status="approved", items=[_APPROVED])
        body = json.loads(r["body"])
        assert len(body["approvals"]) == 1
        assert body["approvals"][0]["approval_id"] == "appr_1"

    def test_approved_empty_when_no_records(self):
        r, _ = self._call(status="approved", items=[])
        body = json.loads(r["body"])
        assert body["approved_commands"] == []

    # --- pending ---

    def test_pending_filters_by_user(self):
        r, apr = self._call(status="pending", items=[_PENDING])
        assert r["statusCode"] == 200
        apr.list_by_agent.assert_called_once_with(AGENT_ID, status="pending", requested_by=USER_ID)

    def test_pending_does_not_include_approved_commands(self):
        r, _ = self._call(status="pending", items=[_PENDING])
        body = json.loads(r["body"])
        assert body["approved_commands"] == []

    # --- denied ---

    def test_denied_filters_by_user(self):
        r, apr = self._call(status="denied", items=[_DENIED])
        assert r["statusCode"] == 200
        apr.list_by_agent.assert_called_once_with(AGENT_ID, status="denied", requested_by=USER_ID)

    # --- expired ---

    def test_expired_queries_repo_with_expired_status(self):
        r, apr = self._call(status="expired", items=[_EXPIRED])
        assert r["statusCode"] == 200
        apr.list_by_agent.assert_called_once_with(AGENT_ID, status="expired", requested_by=USER_ID)

    def test_expired_returns_records_from_repo(self):
        r, _ = self._call(status="expired", items=[_EXPIRED])
        body = json.loads(r["body"])
        assert len(body["approvals"]) == 1
        assert body["approvals"][0]["approval_id"] == "appr_4"

    def test_expired_approved_commands_is_empty(self):
        r, _ = self._call(status="expired", items=[_EXPIRED])
        body = json.loads(r["body"])
        assert body["approved_commands"] == []


# ---------------------------------------------------------------------------
# Lambda handler wrappers
# ---------------------------------------------------------------------------

_OK = {"statusCode": 200, "headers": {}, "body": '{"ok": true}'}
_BEARER = {"authorization": "Bearer tok"}


def _evt(headers=None, path=None, qs=None):
    return {
        "headers": _BEARER if headers is None else headers,
        "pathParameters": path or {},
        "queryStringParameters": qs or {},
    }


class TestListMyPendingHandler:
    def test_delegates(self):
        with patch("handlers.tenant_approvals.handle_list_my_pending", return_value=_OK) as h:
            r = list_my_pending_handler(_evt(qs={"agent_id": AGENT_ID}), None)
        h.assert_called_once_with({"agent_id": AGENT_ID}, "tok")
        assert r == _OK

    def test_missing_auth_returns_401(self):
        r = list_my_pending_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401


class TestListAgentApprovedHandler:
    def test_delegates_default_status(self):
        with patch("handlers.tenant_approvals.handle_list_agent_approved", return_value=_OK) as h:
            list_agent_approved_handler(_evt(path={"agent_id": AGENT_ID}), None)
        h.assert_called_once_with(AGENT_ID, "tok", status="approved")

    def test_delegates_with_status_param(self):
        with patch("handlers.tenant_approvals.handle_list_agent_approved", return_value=_OK) as h:
            list_agent_approved_handler(
                _evt(path={"agent_id": AGENT_ID}, qs={"status": "pending"}),
                None,
            )
        h.assert_called_once_with(AGENT_ID, "tok", status="pending")

    def test_missing_auth_returns_401(self):
        r = list_agent_approved_handler(_evt(headers={}, path={"agent_id": AGENT_ID}), None)
        assert r["statusCode"] == 401
