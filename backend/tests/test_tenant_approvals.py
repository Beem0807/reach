"""Tests for tenant_approvals handler: list_my_pending, list_agent_approved, and Lambda wrappers."""
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from handlers.tenant_approvals import (
    handle_list_my_pending,
    handle_list_agent_approved,
    handle_tenant_list_all_approvals,
    handle_tenant_review_approval,
    handle_tenant_create_approval,
    handle_tenant_delete_approval,
    list_my_pending_handler,
    list_agent_approved_handler,
    list_all_approvals_handler,
    review_approval_handler,
    pre_approve_handler,
    delete_approval_handler,
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
        items = approvals or [_PENDING]
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            agr.get.return_value = agent
            ar.search_by_tenant.return_value = (items, len(items))
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
        assert body["total"] == 1

    def test_scopes_to_requester_and_pending(self):
        r, ar = self._call()
        ar.search_by_tenant.assert_called_once_with(
            TENANT_ID, status="pending", agent_id=None, agent_ids=None, fleet_id=None, fleet_ids=None, requested_by=USER_ID,
            kind=None, q=None, limit=20, offset=0)

    def test_agent_id_filter_passed_through(self):
        r, ar = self._call(query={"agent_id": AGENT_ID})
        ar.search_by_tenant.assert_called_once_with(
            TENANT_ID, status="pending", agent_id=AGENT_ID, agent_ids=None, fleet_id=None, fleet_ids=None, requested_by=USER_ID,
            kind=None, q=None, limit=20, offset=0)

    def test_type_search_and_paging_passed_through(self):
        r, ar = self._call(query={"type": "k8s", "q": "team-a", "limit": "5", "offset": "10"})
        ar.search_by_tenant.assert_called_once_with(
            TENANT_ID, status="pending", agent_id=None, agent_ids=None, fleet_id=None, fleet_ids=None, requested_by=USER_ID,
            kind="k8s", q="team-a", limit=5, offset=10)

    def test_invalid_type_returns_400(self):
        r, _ = self._call(query={"type": "vm"})
        assert r["statusCode"] == 400

    def test_agent_id_not_found_returns_404(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_USER), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo"):
            agr.get.return_value = None
            r = handle_list_my_pending({"agent_id": AGENT_ID}, "tok")
        assert r["statusCode"] == 404

    def test_approved_all_agents_unrestricted_not_scoped(self):
        # An unrestricted developer's "all agents" approved view is tenant-wide.
        r, ar = self._call(query={"status": "approved"}, approvals=[_APPROVED])
        _, kwargs = ar.search_by_tenant.call_args
        assert kwargs["status"] == "approved"
        assert kwargs["agent_id"] is None
        assert kwargs["agent_ids"] is None
        assert kwargs["requested_by"] is None  # approved is shared, not per-requester

    def test_approved_all_agents_restricted_scoped_to_allowed(self):
        # A restricted developer's "all agents" approved view is limited to their agents.
        restricted = {**_USER, "readwrite_agent_ids": [AGENT_ID]}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=restricted), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            ar.search_by_tenant.return_value = ([], 0)
            agr.list_by_tenant.return_value = [
                {"agent_id": AGENT_ID, "tenant_id": TENANT_ID},
                {"agent_id": "agent_b", "tenant_id": TENANT_ID},
            ]
            handle_list_my_pending({"status": "approved"}, "tok")
        _, kwargs = ar.search_by_tenant.call_args
        assert kwargs["agent_ids"] == [AGENT_ID]

    def test_agent_id_no_access_returns_404(self):
        no_access_user = {**_USER, "readwrite_agent_ids": ["agent_other"]}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=no_access_user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo"):
            agr.get.return_value = _AGENT
            r = handle_list_my_pending({"agent_id": AGENT_ID}, "tok")
        assert r["statusCode"] == 404


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
        apr.list_by_agent.assert_called_once_with(AGENT_ID, status="approved", requested_by=None)

    def test_fleet_member_draws_from_fleet(self):
        # A member's effective approved list comes from its fleet, not per-agent.
        member = {**_AGENT, "fleet_id": "fleet_a"}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_USER), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.can_access_agent", return_value=True), \
             patch("handlers.tenant_approvals.approvals_repo") as apr:
            agr.get.return_value = member
            apr.list_by_fleet.return_value = [_APPROVED]
            r = handle_list_agent_approved(AGENT_ID, "tok", status="approved")
        assert r["statusCode"] == 200
        apr.list_by_fleet.assert_called_once_with("fleet_a", status="approved", requested_by=None)
        apr.list_by_agent.assert_not_called()

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


# ---------------------------------------------------------------------------
# handle_tenant_list_all_approvals
# ---------------------------------------------------------------------------

_OPERATOR = {**_USER, "user_id": "user_op", "role": "operator"}
_ADMIN    = {**_USER, "user_id": "user_admin", "role": "admin"}
_DEV      = {**_USER, "user_id": "user_dev", "role": "developer"}

_FULL_APPROVAL = {
    **_PENDING,
    "tenant_id": TENANT_ID,
    "reviewed_by": None,
    "reviewed_at": None,
    "requester_name": "Alice",
}


class TestHandleTenantListAllApprovals:
    def _call(self, user=_OPERATOR, query=None, approvals=None, agent=_AGENT):
        items = approvals if approvals is not None else [_FULL_APPROVAL]
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_approvals.approvals_repo") as ar, \
             patch("handlers.tenant_approvals.agents_repo") as agr:
            ar.search_by_tenant.return_value = (items, len(items))
            agr.get.return_value = agent
            r = handle_tenant_list_all_approvals(query or {}, "tok")
        return r, ar

    def test_unauthorized(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=None):
            r = handle_tenant_list_all_approvals({}, "bad")
        assert r["statusCode"] == 401

    def test_developer_forbidden(self):
        r, _ = self._call(user=_DEV)
        assert r["statusCode"] == 403

    def test_operator_can_list(self):
        r, _ = self._call(user=_OPERATOR)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert len(body["approvals"]) == 1

    def test_admin_can_list(self):
        r, _ = self._call(user=_ADMIN)
        assert r["statusCode"] == 200

    def test_restricted_operator_scoped_to_allowed_agents(self):
        # A restricted operator's "all agents" list is limited to their agents.
        restricted = {**_OPERATOR, "readwrite_agent_ids": [AGENT_ID]}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=restricted), \
             patch("handlers.tenant_approvals.approvals_repo") as ar, \
             patch("handlers.tenant_approvals.agents_repo") as agr:
            ar.search_by_tenant.return_value = ([], 0)
            agr.list_by_tenant.return_value = [
                {"agent_id": AGENT_ID, "tenant_id": TENANT_ID},
                {"agent_id": "agent_b", "tenant_id": TENANT_ID},
            ]
            handle_tenant_list_all_approvals({}, "tok")
        _, kwargs = ar.search_by_tenant.call_args
        assert kwargs["agent_ids"] == [AGENT_ID]

    def test_restricted_operator_specific_inaccessible_agent_sees_nothing(self):
        restricted = {**_OPERATOR, "readwrite_agent_ids": [AGENT_ID]}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=restricted), \
             patch("handlers.tenant_approvals.approvals_repo") as ar, \
             patch("handlers.tenant_approvals.agents_repo") as agr:
            ar.search_by_tenant.return_value = ([], 0)
            agr.list_by_tenant.return_value = [{"agent_id": AGENT_ID, "tenant_id": TENANT_ID}]
            handle_tenant_list_all_approvals({"agent_id": "agent_b"}, "tok")
        _, kwargs = ar.search_by_tenant.call_args
        assert kwargs["agent_ids"] == []

    def test_status_filter_passed_to_repo(self):
        r, ar = self._call(query={"status": "approved"}, approvals=[_APPROVED])
        ar.search_by_tenant.assert_called_once_with(
            TENANT_ID, status="approved", agent_id=None, agent_ids=None, fleet_id=None, fleet_ids=None, scope=None, kind=None, q=None, limit=20, offset=0)

    def test_no_status_filter_passes_none(self):
        r, ar = self._call(query={})
        ar.search_by_tenant.assert_called_once_with(
            TENANT_ID, status=None, agent_id=None, agent_ids=None, fleet_id=None, fleet_ids=None, scope=None, kind=None, q=None, limit=20, offset=0)

    def test_type_and_search_and_paging_passed_to_repo(self):
        r, ar = self._call(query={"type": "k8s", "q": "team-a", "limit": "10", "offset": "20"})
        ar.search_by_tenant.assert_called_once_with(
            TENANT_ID, status=None, agent_id=None, agent_ids=None, fleet_id=None, fleet_ids=None, scope=None, kind="k8s", q="team-a", limit=10, offset=20)

    def test_invalid_type_returns_400(self):
        r, _ = self._call(query={"type": "vm"})
        assert r["statusCode"] == 400

    def test_total_returned_in_body(self):
        r, _ = self._call(query={"limit": "5"}, approvals=[_FULL_APPROVAL])
        body = json.loads(r["body"])
        assert body["total"] == 1 and body["limit"] == 5 and body["offset"] == 0

    def test_agent_hostname_enriched(self):
        agent_with_host = {**_AGENT, "hostname": "prod-01.local"}
        approval_with_agent = {**_FULL_APPROVAL, "agent_id": AGENT_ID}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_OPERATOR), \
             patch("handlers.tenant_approvals.approvals_repo") as ar, \
             patch("handlers.tenant_approvals.agents_repo") as agr:
            ar.search_by_tenant.return_value = ([approval_with_agent], 1)
            agr.get.return_value = agent_with_host
            r = handle_tenant_list_all_approvals({}, "tok")
        body = json.loads(r["body"])
        assert body["approvals"][0]["agent_hostname"] == "prod-01.local"

    def test_agent_hostname_none_when_agent_missing(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_OPERATOR), \
             patch("handlers.tenant_approvals.approvals_repo") as ar, \
             patch("handlers.tenant_approvals.agents_repo") as agr:
            ar.search_by_tenant.return_value = ([_FULL_APPROVAL], 1)
            agr.get.return_value = None
            r = handle_tenant_list_all_approvals({}, "tok")
        body = json.loads(r["body"])
        assert body["approvals"][0]["agent_hostname"] is None

    def test_empty_list_returned(self):
        r, _ = self._call(approvals=[])
        assert json.loads(r["body"])["approvals"] == []


# ---------------------------------------------------------------------------
# handle_tenant_review_approval
# ---------------------------------------------------------------------------

_PENDING_APPROVAL = {
    **_FULL_APPROVAL,
    "approval_id": "appr_rev",
    "tenant_id": TENANT_ID,
    "status": "pending",
}
_DENIED_APPROVAL  = {**_PENDING_APPROVAL, "status": "denied",  "reviewed_at": "2026-01-01T00:00:00"}
_EXPIRED_APPROVAL = {**_PENDING_APPROVAL, "status": "expired", "expires_at": "2020-01-01T00:00:00"}


class TestHandleTenantReviewApproval:
    def _call(self, action="approve", user=_OPERATOR, approval=_PENDING_APPROVAL, body=None, agent=_AGENT):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            agr.get.return_value = agent
            ar.get.return_value = approval
            ar.update_status.return_value = None
            r = handle_tenant_review_approval("appr_rev", action, "tok", body)
        return r, ar

    def test_unauthorized(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=None):
            r = handle_tenant_review_approval("appr_rev", "approve", "bad")
        assert r["statusCode"] == 401

    def test_developer_forbidden(self):
        r, _ = self._call(user=_DEV)
        assert r["statusCode"] == 403

    def test_operator_can_approve(self):
        r, ar = self._call(action="approve")
        assert r["statusCode"] == 200
        ar.update_status.assert_called_once()
        _, args, _ = ar.update_status.mock_calls[0]
        assert args[1] == "approved"

    def test_operator_can_deny(self):
        r, ar = self._call(action="deny")
        assert r["statusCode"] == 200
        _, args, _ = ar.update_status.mock_calls[0]
        assert args[1] == "denied"

    def test_restricted_operator_cannot_review_inaccessible_agent(self):
        # Operator scoped to a different agent than the approval's → hidden (404).
        restricted = {**_OPERATOR, "readwrite_agent_ids": ["other_agent"]}
        r, ar = self._call(user=restricted)
        assert r["statusCode"] == 404
        ar.update_status.assert_not_called()

    def test_restricted_operator_can_review_allowed_agent(self):
        restricted = {**_OPERATOR, "readwrite_agent_ids": [AGENT_ID]}
        r, ar = self._call(user=restricted)
        assert r["statusCode"] == 200
        ar.update_status.assert_called_once()

    def test_readonly_operator_cannot_review(self):
        # Read-only access to the agent → can view but not approve/deny (approvals gate writes).
        ro = {**_OPERATOR, "readwrite_agent_ids": [], "readonly_agent_ids": [AGENT_ID]}
        r, ar = self._call(user=ro)
        assert r["statusCode"] == 403
        assert "read-only" in json.loads(r["body"])["error"]
        ar.update_status.assert_not_called()

    def _call_capturing_audit(self, action="approve", approval=_PENDING_APPROVAL, body=None):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_OPERATOR), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar, \
             patch("handlers.tenant_approvals.audit") as mock_audit:
            agr.get.return_value = _AGENT
            ar.get.return_value = approval
            ar.update_status.return_value = None
            handle_tenant_review_approval("appr_rev", action, "tok", body)
        return mock_audit

    def test_approve_writes_approval_approved_audit(self):
        mock_audit = self._call_capturing_audit(action="approve")
        mock_audit.write.assert_called_once()
        kw = mock_audit.write.call_args.kwargs
        assert mock_audit.write.call_args.args[0] == "approval.approved"
        assert kw["resource_type"] == "approval"
        assert kw["resource_id"] == "appr_rev"
        assert kw["actor_id"] == "user_op"

    def test_deny_writes_approval_denied_audit(self):
        mock_audit = self._call_capturing_audit(action="deny")
        assert mock_audit.write.call_args.args[0] == "approval.denied"

    def test_duration_now_writes_approval_expired_audit(self):
        approved = {**_PENDING_APPROVAL, "status": "approved"}
        mock_audit = self._call_capturing_audit(action="approve", approval=approved, body={"duration": "now"})
        assert mock_audit.write.call_args.args[0] == "approval.expired"

    def test_approval_not_found_returns_404(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_OPERATOR), \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            ar.get.return_value = None
            r = handle_tenant_review_approval("appr_rev", "approve", "tok")
        assert r["statusCode"] == 404

    def test_cross_tenant_returns_404(self):
        wrong_tenant = {**_PENDING_APPROVAL, "tenant_id": "tenant_other"}
        r, _ = self._call(approval=wrong_tenant)
        assert r["statusCode"] == 404

    def test_already_denied_returns_409(self):
        r, _ = self._call(approval=_DENIED_APPROVAL)
        assert r["statusCode"] == 409

    def test_already_expired_returns_409(self):
        r, _ = self._call(approval=_EXPIRED_APPROVAL)
        assert r["statusCode"] == 409

    def test_approve_with_duration_sets_expires_at(self):
        r, ar = self._call(action="approve", body={"duration": "1h"})
        assert r["statusCode"] == 200
        _, args, kwargs = ar.update_status.mock_calls[0]
        expires = kwargs.get("expires_at") or args[4]
        assert expires is not None

    def test_approve_permanent_sets_no_expiry(self):
        r, ar = self._call(action="approve", body={"duration": "permanent"})
        assert r["statusCode"] == 200
        _, args, kwargs = ar.update_status.mock_calls[0]
        expires = kwargs.get("expires_at") if kwargs else args[4]
        assert expires is None

    def test_invalid_duration_returns_400(self):
        r, _ = self._call(action="approve", body={"duration": "forever"})
        assert r["statusCode"] == 400

    def test_deny_already_approved_returns_409(self):
        # Line 137: approved cannot be denied
        approved = {**_PENDING_APPROVAL, "status": "approved"}
        r, _ = self._call(action="deny", approval=approved)
        assert r["statusCode"] == 409

    def test_duration_now_on_pending_returns_400(self):
        # Line 146: now is not valid for initial approval
        r, _ = self._call(action="approve", approval=_PENDING_APPROVAL, body={"duration": "now"})
        assert r["statusCode"] == 400

    def test_duration_now_on_approved_sets_expired(self):
        # Line 151: duration=now on approved → status=expired
        approved = {**_PENDING_APPROVAL, "status": "approved"}
        r, ar = self._call(action="approve", approval=approved, body={"duration": "now"})
        assert r["statusCode"] == 200
        _, args, _ = ar.update_status.mock_calls[0]
        assert args[1] == "expired"

    def test_custom_nh_duration_accepted(self):
        # Lines 34-35: custom duration e.g. "2h"
        r, ar = self._call(action="approve", body={"duration": "2h"})
        assert r["statusCode"] == 200
        _, args, kwargs = ar.update_status.mock_calls[0]
        expires = kwargs.get("expires_at") or args[4]
        assert expires is not None

    def test_custom_nd_duration_accepted(self):
        # Lines 34-35: custom duration e.g. "3d"
        r, ar = self._call(action="approve", body={"duration": "3d"})
        assert r["statusCode"] == 200

    def test_duration_now_on_non_pending_approved_approval(self):
        # Line 27: _parse_expires_at("now") path exercised
        approved = {**_PENDING_APPROVAL, "status": "approved"}
        r, ar = self._call(action="approve", approval=approved, body={"duration": "now"})
        assert r["statusCode"] == 200


# ---------------------------------------------------------------------------
# Lambda wrappers for new handlers
# ---------------------------------------------------------------------------

class TestListAllApprovalsHandler:
    def test_delegates(self):
        with patch("handlers.tenant_approvals.handle_tenant_list_all_approvals", return_value=_OK) as h:
            list_all_approvals_handler(_evt(qs={"status": "pending"}), None)
        h.assert_called_once_with({"status": "pending"}, "tok")

    def test_missing_auth_returns_401(self):
        r = list_all_approvals_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401


class TestReviewApprovalHandler:
    def test_delegates_approve(self):
        with patch("handlers.tenant_approvals.handle_tenant_review_approval", return_value=_OK) as h:
            review_approval_handler(
                _evt(path={"approval_id": "appr_1", "action": "approve"}, qs={}),
                None,
            )
        h.assert_called_once_with("appr_1", "approve", "tok", {})

    def test_missing_auth_returns_401(self):
        r = review_approval_handler(_evt(headers={}, path={"approval_id": "a", "action": "approve"}), None)
        assert r["statusCode"] == 401

    def test_invalid_json_returns_400(self):
        r = review_approval_handler(
            {
                "headers": _BEARER,
                "pathParameters": {"approval_id": "appr_1", "action": "approve"},
                "queryStringParameters": {},
                "body": "not-json",
            },
            None,
        )
        assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# handle_tenant_create_approval
# ---------------------------------------------------------------------------

class TestHandleTenantCreateApproval:
    def _call(self, body=None, user=_OPERATOR, agent=_AGENT, active_approvals=None):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            agr.get.return_value = agent
            ar.list_by_agent.return_value = active_approvals or []
            ar.create.return_value = None
            r = handle_tenant_create_approval(body or {}, "tok")
        return r, ar

    def test_unauthorized(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=None):
            r = handle_tenant_create_approval({}, "bad")
        assert r["statusCode"] == 401

    def test_developer_creates_pending(self):
        r, ar = self._call(user=_DEV, body={"agent_id": AGENT_ID, "command": "ls"})
        assert r["statusCode"] == 201
        assert json.loads(r["body"])["status"] == "pending"
        ar.create.assert_called_once()

    def test_developer_request_with_shell_operators_rejected(self):
        r, ar = self._call(user=_DEV, body={"agent_id": AGENT_ID, "command": "docker restart nginx | tee /etc/x"})
        assert r["statusCode"] == 400
        assert "shell operators" in json.loads(r["body"])["error"]
        ar.create.assert_not_called()

    def test_operator_preapprove_with_shell_operators_rejected(self):
        r, ar = self._call(user=_OPERATOR, body={"agent_id": AGENT_ID, "command": "systemctl restart nginx && rm -rf /"})
        assert r["statusCode"] == 400
        ar.create.assert_not_called()

    def test_developer_creates_pending_with_host_rule(self):
        r, ar = self._call(user=_DEV, body={"agent_id": AGENT_ID,
                                            "host_rule": {"bin": "systemctl", "args": ["restart", "*"]}})
        assert r["statusCode"] == 201
        rec = ar.create.call_args[0][0]
        assert rec["host_rule"] == {"bin": "systemctl", "args": ["restart", "*"]}
        assert rec["command"] == "systemctl restart *"    # display form
        assert rec["k8s_rule"] is None

    def test_developer_invalid_host_rule_rejected(self):
        r, ar = self._call(user=_DEV, body={"agent_id": AGENT_ID, "host_rule": {"args": ["x"]}})
        assert r["statusCode"] == 400
        ar.create.assert_not_called()

    def test_operator_preapprove_host_rule(self):
        r, ar = self._call(user=_OPERATOR, body={"agent_id": AGENT_ID,
                                                 "host_rule": {"bin": "df", "args": ["-h"]}})
        assert r["statusCode"] == 200   # operator pre-approve returns 200
        rec = ar.create.call_args[0][0]
        assert rec["host_rule"] == {"bin": "df", "args": ["-h"]}
        assert rec["command"] == "df -h"

    def test_developer_already_approved_command_returns_409(self):
        existing = {**_APPROVED, "command": "ls"}
        r, _ = self._call(user=_DEV, body={"agent_id": AGENT_ID, "command": "ls"}, active_approvals=[existing])
        assert r["statusCode"] == 409

    def test_developer_already_pending_returns_409(self):
        existing_pending = {**_PENDING, "command": "ls"}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_DEV), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            agr.get.return_value = _AGENT
            ar.list_by_agent.side_effect = [[], [existing_pending]]
            r = handle_tenant_create_approval({"agent_id": AGENT_ID, "command": "ls"}, "tok")
        assert r["statusCode"] == 409

    def test_developer_missing_command_returns_400(self):
        r, _ = self._call(user=_DEV, body={"agent_id": AGENT_ID})
        assert r["statusCode"] == 400

    def test_readonly_user_cannot_create_approval(self):
        # A read-only grant on the agent blocks creating approvals (they're always
        # for writes), even though the user can access the agent.
        ro_dev = {**_DEV, "readonly_agent_ids": [AGENT_ID]}
        r, ar = self._call(user=ro_dev, body={"agent_id": AGENT_ID, "command": "rm x"})
        assert r["statusCode"] == 403
        assert "read-only" in json.loads(r["body"])["error"]
        ar.create.assert_not_called()

    def test_readwrite_user_can_create_approval(self):
        rw_dev = {**_DEV, "readwrite_agent_ids": [AGENT_ID]}
        r, ar = self._call(user=rw_dev, body={"agent_id": AGENT_ID, "command": "ls"})
        assert r["statusCode"] == 201

    def test_missing_agent_id_returns_400(self):
        r, _ = self._call(body={"command": "ls"})
        assert r["statusCode"] == 400

    def test_agent_not_found_returns_404(self):
        r, _ = self._call(body={"agent_id": AGENT_ID, "command": "ls"}, agent=None)
        assert r["statusCode"] == 404

    def test_restricted_user_cannot_request_inaccessible_agent(self):
        restricted = {**_DEV, "readwrite_agent_ids": ["other_agent"]}
        r, ar = self._call(user=restricted, body={"agent_id": AGENT_ID, "command": "ls"})
        assert r["statusCode"] == 404
        ar.create.assert_not_called()

    def test_missing_command_returns_400(self):
        r, _ = self._call(body={"agent_id": AGENT_ID})
        assert r["statusCode"] == 400

    def test_duration_now_rejected_400(self):
        r, _ = self._call(body={"agent_id": AGENT_ID, "command": "ls", "duration": "now"})
        assert r["statusCode"] == 400

    def test_single_already_approved_command_returns_409(self):
        existing = {**_APPROVED, "command": "ls"}
        r, _ = self._call(
            body={"agent_id": AGENT_ID, "command": "ls"},
            active_approvals=[existing],
        )
        assert r["statusCode"] == 409

    def test_success_single_command(self):
        r, ar = self._call(body={"agent_id": AGENT_ID, "command": "ls"})
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["command"] == "ls"
        assert body["status"] == "approved"
        ar.create.assert_called_once()

    def test_success_bulk_commands(self):
        r, ar = self._call(body={"agent_id": AGENT_ID, "commands": ["ls", "pwd"]})
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert "created" in body
        assert "skipped" in body
        assert len(body["created"]) == 2

    def test_bulk_skips_already_approved(self):
        existing = {**_APPROVED, "command": "ls"}
        r, ar = self._call(
            body={"agent_id": AGENT_ID, "commands": ["ls", "pwd"]},
            active_approvals=[existing],
        )
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert len(body["created"]) == 1
        assert len(body["skipped"]) == 1
        assert body["skipped"][0]["command"] == "ls"

    def test_empty_commands_list_returns_400(self):
        r, _ = self._call(body={"agent_id": AGENT_ID, "commands": []})
        assert r["statusCode"] == 400

    def test_commands_list_with_only_whitespace_returns_400(self):
        # Line 182: all strings are empty/whitespace after stripping
        r, _ = self._call(body={"agent_id": AGENT_ID, "commands": ["   ", ""]})
        assert r["statusCode"] == 400

    def test_invalid_duration_returns_400(self):
        r, _ = self._call(body={"agent_id": AGENT_ID, "command": "ls", "duration": "forever"})
        assert r["statusCode"] == 400

    def test_agent_from_different_tenant_returns_404(self):
        other_tenant_agent = {"agent_id": AGENT_ID, "tenant_id": "other_tenant"}
        r, _ = self._call(
            body={"agent_id": AGENT_ID, "command": "ls"},
            agent=other_tenant_agent,
        )
        assert r["statusCode"] == 404

    # --- k8s structured rules ---------------------------------------------
    _AGENT_K8S = {"agent_id": AGENT_ID, "tenant_id": TENANT_ID, "type": "k8s"}
    _RULE = {"verb": "delete", "resource": "pods", "namespace": "team-a", "name": "*"}

    def test_k8s_developer_requires_rule_not_command(self):
        r, _ = self._call(user=_DEV, agent=self._AGENT_K8S, body={"agent_id": AGENT_ID, "command": "kubectl delete pod x"})
        assert r["statusCode"] == 400

    def test_k8s_developer_creates_pending_with_rule(self):
        r, ar = self._call(user=_DEV, agent=self._AGENT_K8S, body={"agent_id": AGENT_ID, "k8s_rule": self._RULE})
        assert r["statusCode"] == 201
        stored = ar.create.call_args[0][0]
        assert stored["status"] == "pending"
        assert stored["k8s_rule"] == self._RULE

    def test_k8s_operator_preapproves_rule(self):
        r, ar = self._call(agent=self._AGENT_K8S, body={"agent_id": AGENT_ID, "k8s_rule": self._RULE})
        assert r["statusCode"] == 200
        stored = ar.create.call_args[0][0]
        assert stored["status"] == "approved"
        assert stored["k8s_rule"] == self._RULE

    def test_k8s_operator_missing_rule_returns_400(self):
        r, _ = self._call(agent=self._AGENT_K8S, body={"agent_id": AGENT_ID})
        assert r["statusCode"] == 400

    def test_k8s_operator_read_verb_rule_rejected(self):
        r, _ = self._call(agent=self._AGENT_K8S, body={"agent_id": AGENT_ID, "k8s_rule": {"verb": "get"}})
        assert r["statusCode"] == 400

    def test_k8s_operator_bulk_rules(self):
        rules = [self._RULE, {"verb": "create", "resource": "pods", "namespace": "team-a", "name": "*"}]
        r, ar = self._call(agent=self._AGENT_K8S, body={"agent_id": AGENT_ID, "k8s_rules": rules})
        assert r["statusCode"] == 200
        assert len(json.loads(r["body"])["created"]) == 2

    def test_k8s_operator_skips_already_approved_rule(self):
        existing = {**_APPROVED, "command": "kubectl delete pods -n team-a", "k8s_rule": self._RULE}
        r, _ = self._call(agent=self._AGENT_K8S, body={"agent_id": AGENT_ID, "k8s_rule": self._RULE}, active_approvals=[existing])
        assert r["statusCode"] == 409

    def test_k8s_wildcard_rule_accepted(self):
        rule = {"verb": "*", "resource": "*", "namespace": "team-a", "name": "*"}
        r, ar = self._call(agent=self._AGENT_K8S, body={"agent_id": AGENT_ID, "k8s_rule": rule})
        assert r["statusCode"] == 200
        assert ar.create.call_args[0][0]["k8s_rule"] == rule


# ---------------------------------------------------------------------------
# handle_tenant_delete_approval
# ---------------------------------------------------------------------------

class TestHandleTenantDeleteApproval:
    def _call(self, approval=None, user=_ADMIN, agent=_AGENT):
        the_approval = approval or {**_FULL_APPROVAL, "approval_id": "appr_del", "tenant_id": TENANT_ID}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            agr.get.return_value = agent
            ar.get.return_value = the_approval
            ar.delete.return_value = None
            r = handle_tenant_delete_approval("appr_del", "tok")
        return r, ar

    def test_unauthorized(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=None):
            r = handle_tenant_delete_approval("appr_del", "bad")
        assert r["statusCode"] == 401

    def test_developer_forbidden(self):
        dev = {**_USER, "user_id": "user_dev", "role": "developer"}
        r, _ = self._call(user=dev)
        assert r["statusCode"] == 403

    def test_operator_can_delete(self):
        r, ar = self._call(user=_OPERATOR)
        assert r["statusCode"] == 200
        ar.delete.assert_called_once_with("appr_del")

    def test_restricted_operator_cannot_delete_inaccessible_agent(self):
        restricted = {**_OPERATOR, "readwrite_agent_ids": ["other_agent"]}
        r, ar = self._call(user=restricted)
        assert r["statusCode"] == 404
        ar.delete.assert_not_called()

    def test_can_delete_fleet_approval(self):
        # A fleet approval has agent_id=None; delete must resolve the fleet, not the
        # (missing) agent - otherwise every fleet approval is undeletable.
        fleet_appr = {**_FULL_APPROVAL, "approval_id": "appr_del", "tenant_id": TENANT_ID,
                      "agent_id": None, "fleet_id": "fleet_1"}
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_OPERATOR), \
             patch("handlers.tenant_approvals.fleets_repo") as fr, \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            fr.get.return_value = {"fleet_id": "fleet_1", "tenant_id": TENANT_ID, "name": "web"}
            ar.get.return_value = fleet_appr
            r = handle_tenant_delete_approval("appr_del", "tok")
        assert r["statusCode"] == 200
        ar.delete.assert_called_once_with("appr_del")
        agr.get.assert_not_called()  # must NOT look up an agent for a fleet approval

    def test_approval_not_found_returns_404(self):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=_ADMIN), \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            ar.get.return_value = None
            r = handle_tenant_delete_approval("missing", "tok")
        assert r["statusCode"] == 404

    def test_cross_tenant_returns_404(self):
        other = {**_FULL_APPROVAL, "approval_id": "appr_del", "tenant_id": "other_tenant"}
        r, _ = self._call(approval=other)
        assert r["statusCode"] == 404

    def test_success_deletes(self):
        r, ar = self._call()
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["deleted"] is True
        ar.delete.assert_called_once_with("appr_del")


# ---------------------------------------------------------------------------
# pre_approve_handler and delete_approval_handler Lambda wrappers
# ---------------------------------------------------------------------------

def _evt_with_body(body_str=None, headers=None, path=None, qs=None):
    return {
        "headers": _BEARER if headers is None else headers,
        "pathParameters": path or {},
        "queryStringParameters": qs or {},
        "body": body_str,
    }


class TestPreApproveHandler:
    def test_missing_auth_returns_401(self):
        r = pre_approve_handler(_evt_with_body(headers={}), None)
        assert r["statusCode"] == 401

    def test_delegates(self):
        body = {"agent_id": AGENT_ID, "command": "ls"}
        with patch("handlers.tenant_approvals.handle_tenant_create_approval", return_value=_OK) as h:
            pre_approve_handler(_evt_with_body(body_str=json.dumps(body)), None)
        h.assert_called_once_with(body, "tok")

    def test_invalid_json_returns_400(self):
        r = pre_approve_handler(_evt_with_body(body_str="not-json"), None)
        assert r["statusCode"] == 400


class TestDeleteApprovalHandler:
    def test_missing_auth_returns_401(self):
        r = delete_approval_handler(_evt(headers={}, path={"approval_id": "appr_1"}), None)
        assert r["statusCode"] == 401

    def test_delegates(self):
        with patch("handlers.tenant_approvals.handle_tenant_delete_approval", return_value=_OK) as h:
            delete_approval_handler(_evt(path={"approval_id": "appr_1"}), None)
        h.assert_called_once_with("appr_1", "tok")


# ---------------------------------------------------------------------------
# Fleet-scoped approvals
# ---------------------------------------------------------------------------

FLEET_ID = "fleet_a"
_FLEET = {"fleet_id": FLEET_ID, "tenant_id": TENANT_ID, "name": "web-asg"}
# A fleet member: an agent that belongs to a fleet (approvals live at the fleet).
_MEMBER_AGENT = {"agent_id": "agent_m", "tenant_id": TENANT_ID, "fleet_id": FLEET_ID}
_FLEET_OPERATOR = {**_OPERATOR, "readwrite_fleet_ids": [FLEET_ID], "readonly_fleet_ids": []}


class TestFleetScopedCreate:
    def _call(self, body=None, user=_OPERATOR, fleet=_FLEET, agent=_AGENT, active=None):
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.fleets_repo") as fr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            agr.get.return_value = agent
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = active or []
            ar.create.return_value = None
            r = handle_tenant_create_approval(body or {}, "tok")
        return r, ar

    def test_operator_preapproves_fleet_approval(self):
        # An operator with write access to the fleet pre-approves (status approved).
        r, ar = self._call(user=_FLEET_OPERATOR, body={"fleet_id": FLEET_ID, "command": "docker ps"})
        assert r["statusCode"] == 200
        ar.create.assert_called_once()
        created = ar.create.mock_calls[0].args[0]
        assert created["fleet_id"] == FLEET_ID and created["agent_id"] is None
        assert created["status"] == "approved"

    def test_operator_preapproves_fleet_host_rule(self):
        # Fleet approvals can be structured JSON rules (fleets are host-only).
        r, ar = self._call(user=_FLEET_OPERATOR,
                           body={"fleet_id": FLEET_ID, "host_rule": {"bin": "systemctl", "args": ["restart", "*"]}})
        assert r["statusCode"] == 200
        created = ar.create.mock_calls[0].args[0]
        assert created["fleet_id"] == FLEET_ID
        assert created["host_rule"] == {"bin": "systemctl", "args": ["restart", "*"]}
        assert created["command"] == "systemctl restart *"

    def test_developer_creates_pending_fleet_approval(self):
        dev = {**_DEV, "readwrite_fleet_ids": [FLEET_ID], "readonly_fleet_ids": []}
        r, ar = self._call(user=dev, body={"fleet_id": FLEET_ID, "command": "docker ps"})
        assert r["statusCode"] == 201
        created = ar.create.mock_calls[0].args[0]
        assert created["fleet_id"] == FLEET_ID and created["status"] == "pending"

    def test_agent_and_fleet_both_returns_400(self):
        r, _ = self._call(body={"agent_id": AGENT_ID, "fleet_id": FLEET_ID, "command": "ls"})
        assert r["statusCode"] == 400

    def test_neither_agent_nor_fleet_returns_400(self):
        r, _ = self._call(body={"command": "ls"})
        assert r["statusCode"] == 400

    def test_fleet_member_agent_rejected(self):
        # Approving a command on a fleet member's agent_id is not allowed - use the fleet.
        r, _ = self._call(body={"agent_id": "agent_m", "command": "ls"}, agent=_MEMBER_AGENT)
        assert r["statusCode"] == 409
        assert "fleet member" in json.loads(r["body"])["error"]

    def test_unknown_fleet_returns_404(self):
        r, _ = self._call(user=_FLEET_OPERATOR, body={"fleet_id": FLEET_ID, "command": "ls"}, fleet=None)
        assert r["statusCode"] == 404

    def test_readonly_fleet_cannot_create(self):
        ro = {**_OPERATOR, "readwrite_fleet_ids": [], "readonly_fleet_ids": [FLEET_ID]}
        r, ar = self._call(user=ro, body={"fleet_id": FLEET_ID, "command": "ls"})
        assert r["statusCode"] == 403
        ar.create.assert_not_called()

    def test_inaccessible_fleet_hidden(self):
        scoped = {**_OPERATOR, "readwrite_fleet_ids": ["other_fleet"], "readonly_fleet_ids": []}
        r, _ = self._call(user=scoped, body={"fleet_id": FLEET_ID, "command": "ls"})
        assert r["statusCode"] in (403, 404)

    def test_duplicate_fleet_approval_returns_409(self):
        existing = {**_APPROVED, "command": "docker ps", "fleet_id": FLEET_ID, "agent_id": None}
        r, _ = self._call(user=_FLEET_OPERATOR, body={"fleet_id": FLEET_ID, "command": "docker ps"}, active=[existing])
        assert r["statusCode"] == 409


class TestFleetScopedReview:
    _FLEET_PENDING = {**_PENDING_APPROVAL, "agent_id": None, "fleet_id": FLEET_ID}

    def _call(self, action="approve", user=_FLEET_OPERATOR, approval=None, fleet=_FLEET):
        approval = approval or self._FLEET_PENDING
        with patch("handlers.tenant_approvals._verify_tenant_token", return_value=user), \
             patch("handlers.tenant_approvals.agents_repo") as agr, \
             patch("handlers.tenant_approvals.fleets_repo") as fr, \
             patch("handlers.tenant_approvals.approvals_repo") as ar:
            agr.get.return_value = _AGENT
            fr.get.return_value = fleet
            ar.get.return_value = approval
            ar.update_status.return_value = None
            r = handle_tenant_review_approval("appr_rev", action, "tok", None)
        return r, ar

    def test_writable_fleet_operator_can_approve(self):
        r, ar = self._call(action="approve")
        assert r["statusCode"] == 200
        ar.update_status.assert_called_once()

    def test_readonly_fleet_operator_cannot_review(self):
        ro = {**_OPERATOR, "readwrite_fleet_ids": [], "readonly_fleet_ids": [FLEET_ID]}
        r, ar = self._call(user=ro)
        assert r["statusCode"] == 403
        ar.update_status.assert_not_called()

    def test_inaccessible_fleet_hidden(self):
        scoped = {**_OPERATOR, "readwrite_fleet_ids": ["other_fleet"], "readonly_fleet_ids": []}
        r, ar = self._call(user=scoped)
        assert r["statusCode"] == 404
        ar.update_status.assert_not_called()
