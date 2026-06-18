"""
Tests for the Lambda event handler wrappers in each handler module.
Each wrapper: extracts token from Authorization header, extracts path/query
params, parses JSON body, then delegates to the core handle_* function.

Strategy: mock the core handle_* function and verify the wrapper passes
the right args and forwards the return value. Also test missing auth → 401
and invalid JSON → 400 where applicable.
"""
import json
from unittest.mock import patch, MagicMock

ADMIN = "test-admin-token"
_OK = {"statusCode": 200, "headers": {}, "body": '{"ok": true}'}
_BEARER = {"authorization": f"Bearer {ADMIN}"}


def _evt(headers=None, body=None, path=None, qs=None):
    return {
        "headers": _BEARER if headers is None else headers,
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path or {},
        "queryStringParameters": qs or {},
    }


# ---------------------------------------------------------------------------
# admin_tenants
# ---------------------------------------------------------------------------

class TestAdminTenantHandlers:
    def test_create_tenant_handler_delegates(self):
        from handlers.admin_tenants import create_tenant_handler
        with patch("handlers.admin_tenants.handle_create_tenant", return_value=_OK) as h:
            r = create_tenant_handler(_evt(body={"name": "Acme"}), None)
        h.assert_called_once_with({"name": "Acme"}, ADMIN)
        assert r == _OK

    def test_create_tenant_handler_missing_auth(self):
        from handlers.admin_tenants import create_tenant_handler
        r = create_tenant_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_create_tenant_handler_invalid_json(self):
        from handlers.admin_tenants import create_tenant_handler
        evt = _evt()
        evt["body"] = "not-json"
        r = create_tenant_handler(evt, None)
        assert r["statusCode"] == 400

    def test_list_tenants_handler_delegates(self):
        from handlers.admin_tenants import list_tenants_handler
        with patch("handlers.admin_tenants.handle_list_tenants", return_value=_OK) as h:
            r = list_tenants_handler(_evt(), None)
        h.assert_called_once_with(ADMIN)
        assert r == _OK

    def test_list_tenants_handler_missing_auth(self):
        from handlers.admin_tenants import list_tenants_handler
        r = list_tenants_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# admin_users
# ---------------------------------------------------------------------------

class TestAdminUserHandlers:
    def _path(self, **kw):
        return {"tenant_id": "tenant_1", "user_id": "user_1", **kw}

    def test_create_user_handler_delegates(self):
        from handlers.admin_users import create_user_handler
        evt = {**_evt(body={"name": "alice"}, path=self._path()),
               "headers": {**_BEARER, "host": "api.example.com"}}
        with patch("handlers.admin_users.handle_create_user", return_value=_OK) as h:
            create_user_handler(evt, None)
        assert h.call_args[0][0] == "tenant_1"
        assert h.call_args[0][1] == {"name": "alice"}

    def test_create_user_handler_missing_auth(self):
        from handlers.admin_users import create_user_handler
        r = create_user_handler(_evt(headers={}, path=self._path()), None)
        assert r["statusCode"] == 401

    def test_create_user_handler_invalid_json(self):
        from handlers.admin_users import create_user_handler
        evt = _evt(path=self._path()); evt["body"] = "bad"
        r = create_user_handler(evt, None)
        assert r["statusCode"] == 400

    def test_list_users_handler_delegates(self):
        from handlers.admin_users import list_users_handler
        with patch("handlers.admin_users.handle_list_users", return_value=_OK) as h:
            list_users_handler(_evt(path=self._path()), None)
        h.assert_called_once_with("tenant_1", ADMIN)

    def test_list_users_handler_missing_auth(self):
        from handlers.admin_users import list_users_handler
        r = list_users_handler(_evt(headers={}, path=self._path()), None)
        assert r["statusCode"] == 401

    def test_delete_user_handler_delegates(self):
        from handlers.admin_users import delete_user_handler
        with patch("handlers.admin_users.handle_delete_user", return_value=_OK) as h:
            delete_user_handler(_evt(path=self._path()), None)
        h.assert_called_once_with("tenant_1", "user_1", ADMIN)

    def test_delete_user_handler_missing_auth(self):
        from handlers.admin_users import delete_user_handler
        r = delete_user_handler(_evt(headers={}, path=self._path()), None)
        assert r["statusCode"] == 401

    def test_rotate_user_token_handler_delegates(self):
        from handlers.admin_users import rotate_user_token_handler
        evt = {**_evt(path=self._path()), "headers": {**_BEARER, "host": "api.example.com"}}
        with patch("handlers.admin_users.handle_rotate_user_token", return_value=_OK) as h:
            rotate_user_token_handler(evt, None)
        assert h.call_args[0][:2] == ("tenant_1", "user_1")

    def test_rotate_user_token_handler_missing_auth(self):
        from handlers.admin_users import rotate_user_token_handler
        r = rotate_user_token_handler(_evt(headers={}, path=self._path()), None)
        assert r["statusCode"] == 401

    def test_get_user_agents_handler_delegates(self):
        from handlers.admin_users import get_user_agents_handler
        with patch("handlers.admin_users.handle_get_user_agents", return_value=_OK) as h:
            get_user_agents_handler(_evt(path=self._path()), None)
        h.assert_called_once_with("tenant_1", "user_1", ADMIN)

    def test_get_user_agents_handler_missing_auth(self):
        from handlers.admin_users import get_user_agents_handler
        r = get_user_agents_handler(_evt(headers={}, path=self._path()), None)
        assert r["statusCode"] == 401

    def test_set_user_agents_handler_delegates(self):
        from handlers.admin_users import set_user_agents_handler
        with patch("handlers.admin_users.handle_set_user_agents", return_value=_OK) as h:
            set_user_agents_handler(_evt(body={"agent_ids": ["a"]}, path=self._path()), None)
        assert h.call_args[0][:2] == ("tenant_1", "user_1")

    def test_set_user_agents_handler_missing_auth(self):
        from handlers.admin_users import set_user_agents_handler
        r = set_user_agents_handler(_evt(headers={}, path=self._path()), None)
        assert r["statusCode"] == 401

    def test_set_user_agents_handler_invalid_json(self):
        from handlers.admin_users import set_user_agents_handler
        evt = _evt(path=self._path()); evt["body"] = "bad"
        r = set_user_agents_handler(evt, None)
        assert r["statusCode"] == 400

    def test_grant_agent_access_handler_delegates(self):
        from handlers.admin_users import grant_agent_access_handler
        with patch("handlers.admin_users.handle_grant_agent_access", return_value=_OK) as h:
            grant_agent_access_handler(_evt(path={**self._path(), "agent_id": "agent_a"}), None)
        h.assert_called_once_with("tenant_1", "user_1", "agent_a", ADMIN)

    def test_grant_agent_access_handler_missing_auth(self):
        from handlers.admin_users import grant_agent_access_handler
        r = grant_agent_access_handler(_evt(headers={}, path={**self._path(), "agent_id": "a"}), None)
        assert r["statusCode"] == 401

    def test_revoke_agent_access_handler_delegates(self):
        from handlers.admin_users import revoke_agent_access_handler
        with patch("handlers.admin_users.handle_revoke_agent_access", return_value=_OK) as h:
            revoke_agent_access_handler(_evt(path={**self._path(), "agent_id": "agent_a"}), None)
        h.assert_called_once_with("tenant_1", "user_1", "agent_a", ADMIN)

    def test_revoke_agent_access_handler_missing_auth(self):
        from handlers.admin_users import revoke_agent_access_handler
        r = revoke_agent_access_handler(_evt(headers={}, path={**self._path(), "agent_id": "a"}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# admin_agents
# ---------------------------------------------------------------------------

class TestAdminAgentHandlers:
    def test_list_agents_admin_handler_delegates(self):
        from handlers.admin_agents import list_agents_admin_handler
        with patch("handlers.admin_agents.handle_list_agents_admin", return_value=_OK) as h:
            list_agents_admin_handler(_evt(qs={"tenant_id": "t1", "tag": "env:prod"}), None)
        h.assert_called_once_with("t1", ADMIN, "env:prod")

    def test_list_agents_admin_handler_missing_auth(self):
        from handlers.admin_agents import list_agents_admin_handler
        r = list_agents_admin_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_create_agent_handler_delegates(self):
        from handlers.admin_agents import create_agent_handler
        with patch("handlers.admin_agents.handle_create_agent", return_value=_OK) as h:
            create_agent_handler(_evt(body={"tenant_id": "t1"}), None)
        assert h.call_args[0][0] == {"tenant_id": "t1"}

    def test_create_agent_handler_invalid_json(self):
        from handlers.admin_agents import create_agent_handler
        evt = _evt(); evt["body"] = "bad"
        r = create_agent_handler(evt, None)
        assert r["statusCode"] == 400

    def test_reissue_install_token_handler_delegates(self):
        from handlers.admin_agents import reissue_install_token_handler
        with patch("handlers.admin_agents.handle_reissue_install_token", return_value=_OK) as h:
            reissue_install_token_handler(_evt(path={"agent_id": "agent_a"}), None)
        assert h.call_args[0][0] == "agent_a"

    def test_delete_agent_handler_delegates(self):
        from handlers.admin_agents import delete_agent_handler
        with patch("handlers.admin_agents.handle_delete_agent", return_value=_OK) as h:
            delete_agent_handler(_evt(body={"force": True}, path={"agent_id": "agent_a"}), None)
        assert h.call_args[0][0] == "agent_a"

    def test_get_agent_tags_handler_delegates(self):
        from handlers.admin_agents import get_agent_tags_handler
        with patch("handlers.admin_agents.handle_get_agent_tags", return_value=_OK) as h:
            get_agent_tags_handler(_evt(path={"agent_id": "agent_a"}), None)
        h.assert_called_once_with("agent_a", ADMIN)

    def test_set_agent_tags_handler_delegates(self):
        from handlers.admin_agents import set_agent_tags_handler
        with patch("handlers.admin_agents.handle_set_agent_tags", return_value=_OK) as h:
            set_agent_tags_handler(_evt(body={"tags": ["env:prod"]}, path={"agent_id": "agent_a"}), None)
        assert h.call_args[0][0] == "agent_a"
        assert h.call_args[0][1] == {"tags": ["env:prod"]}

    def test_add_agent_tags_handler_delegates(self):
        from handlers.admin_agents import add_agent_tags_handler
        with patch("handlers.admin_agents.handle_add_agent_tags", return_value=_OK) as h:
            add_agent_tags_handler(_evt(body={"tags": ["env:prod"]}, path={"agent_id": "agent_a"}), None)
        assert h.call_args[0][0] == "agent_a"

    def test_remove_agent_tags_handler_delegates(self):
        from handlers.admin_agents import remove_agent_tags_handler
        with patch("handlers.admin_agents.handle_remove_agent_tags", return_value=_OK) as h:
            remove_agent_tags_handler(_evt(body={"tags": ["env:prod"]}, path={"agent_id": "agent_a"}), None)
        assert h.call_args[0][0] == "agent_a"

    def test_create_agent_handler_missing_auth(self):
        from handlers.admin_agents import create_agent_handler
        r = create_agent_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_reissue_install_token_missing_auth(self):
        from handlers.admin_agents import reissue_install_token_handler
        r = reissue_install_token_handler(_evt(headers={}, path={"agent_id": "a"}), None)
        assert r["statusCode"] == 401

    def test_reissue_install_token_invalid_json(self):
        from handlers.admin_agents import reissue_install_token_handler
        evt = _evt(path={"agent_id": "a"}); evt["body"] = "bad"
        r = reissue_install_token_handler(evt, None)
        assert r["statusCode"] == 400

    def test_delete_agent_missing_auth(self):
        from handlers.admin_agents import delete_agent_handler
        r = delete_agent_handler(_evt(headers={}, path={"agent_id": "a"}), None)
        assert r["statusCode"] == 401

    def test_delete_agent_invalid_json(self):
        from handlers.admin_agents import delete_agent_handler
        evt = _evt(path={"agent_id": "a"}); evt["body"] = "bad"
        r = delete_agent_handler(evt, None)
        assert r["statusCode"] == 400

    def test_get_agent_tags_missing_auth(self):
        from handlers.admin_agents import get_agent_tags_handler
        r = get_agent_tags_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_set_agent_tags_missing_auth(self):
        from handlers.admin_agents import set_agent_tags_handler
        r = set_agent_tags_handler(_evt(headers={}, path={"agent_id": "a"}), None)
        assert r["statusCode"] == 401

    def test_set_agent_tags_invalid_json(self):
        from handlers.admin_agents import set_agent_tags_handler
        evt = _evt(path={"agent_id": "a"}); evt["body"] = "bad"
        r = set_agent_tags_handler(evt, None)
        assert r["statusCode"] == 400

    def test_add_agent_tags_missing_auth(self):
        from handlers.admin_agents import add_agent_tags_handler
        r = add_agent_tags_handler(_evt(headers={}, path={"agent_id": "a"}), None)
        assert r["statusCode"] == 401

    def test_add_agent_tags_invalid_json(self):
        from handlers.admin_agents import add_agent_tags_handler
        evt = _evt(path={"agent_id": "a"}); evt["body"] = "bad"
        r = add_agent_tags_handler(evt, None)
        assert r["statusCode"] == 400

    def test_remove_agent_tags_missing_auth(self):
        from handlers.admin_agents import remove_agent_tags_handler
        r = remove_agent_tags_handler(_evt(headers={}, path={"agent_id": "a"}), None)
        assert r["statusCode"] == 401

    def test_remove_agent_tags_invalid_json(self):
        from handlers.admin_agents import remove_agent_tags_handler
        evt = _evt(path={"agent_id": "a"}); evt["body"] = "bad"
        r = remove_agent_tags_handler(evt, None)
        assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# admin_policy
# ---------------------------------------------------------------------------

class TestAdminPolicyHandlers:
    def test_get_policy_handler_delegates(self):
        from handlers.admin_policy import get_policy_handler
        with patch("handlers.admin_policy.handle_get_policy", return_value=_OK) as h:
            get_policy_handler(_evt(path={"agent_id": "agent_a"}), None)
        h.assert_called_once_with("agent_a", ADMIN)

    def test_get_policy_handler_missing_auth(self):
        from handlers.admin_policy import get_policy_handler
        r = get_policy_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_set_mode_handler_delegates(self):
        from handlers.admin_policy import set_mode_handler
        with patch("handlers.admin_policy.handle_set_mode", return_value=_OK) as h:
            set_mode_handler(_evt(body={"mode": "readonly"}, path={"agent_id": "agent_a"}), None)
        h.assert_called_once_with("agent_a", {"mode": "readonly"}, ADMIN)

    def test_set_mode_handler_missing_auth(self):
        from handlers.admin_policy import set_mode_handler
        r = set_mode_handler(_evt(headers={}, path={"agent_id": "a"}), None)
        assert r["statusCode"] == 401

    def test_set_mode_handler_invalid_json(self):
        from handlers.admin_policy import set_mode_handler
        evt = _evt(path={"agent_id": "a"}); evt["body"] = "bad"
        r = set_mode_handler(evt, None)
        assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# admin_approvals
# ---------------------------------------------------------------------------

class TestAdminApprovalHandlers:
    def test_list_approvals_handler_delegates(self):
        from handlers.admin_approvals import list_approvals_handler
        with patch("handlers.admin_approvals.handle_list_approvals", return_value=_OK) as h:
            list_approvals_handler(_evt(qs={"tenant_id": "t1"}), None)
        h.assert_called_once_with({"tenant_id": "t1"}, ADMIN)

    def test_list_approvals_handler_missing_auth(self):
        from handlers.admin_approvals import list_approvals_handler
        r = list_approvals_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_review_approval_handler_delegates_approve(self):
        from handlers.admin_approvals import review_approval_handler
        with patch("handlers.admin_approvals.handle_review_approval", return_value=_OK) as h:
            review_approval_handler(
                _evt(body={"duration": "8h"}, path={"approval_id": "appr_1", "action": "approve"}),
                None,
            )
        h.assert_called_once_with("appr_1", "approve", ADMIN, {"duration": "8h"})

    def test_review_approval_handler_delegates_deny(self):
        from handlers.admin_approvals import review_approval_handler
        with patch("handlers.admin_approvals.handle_review_approval", return_value=_OK) as h:
            review_approval_handler(
                _evt(path={"approval_id": "appr_1", "action": "deny"}),
                None,
            )
        h.assert_called_once_with("appr_1", "deny", ADMIN, {})

    def test_review_approval_handler_missing_auth(self):
        from handlers.admin_approvals import review_approval_handler
        r = review_approval_handler(_evt(headers={}, path={"approval_id": "a", "action": "approve"}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# tenant_approvals
# ---------------------------------------------------------------------------

class TestTenantApprovalHandlers:
    def test_list_my_pending_handler_delegates(self):
        from handlers.tenant_approvals import list_my_pending_handler
        with patch("handlers.tenant_approvals.handle_list_my_pending", return_value=_OK) as h:
            list_my_pending_handler({**_evt(), "headers": {"authorization": "Bearer user-tok"}}, None)
        h.assert_called_once_with({}, "user-tok")

    def test_list_my_pending_handler_missing_auth(self):
        from handlers.tenant_approvals import list_my_pending_handler
        r = list_my_pending_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_list_agent_approved_handler_delegates_default_status(self):
        from handlers.tenant_approvals import list_agent_approved_handler
        with patch("handlers.tenant_approvals.handle_list_agent_approved", return_value=_OK) as h:
            list_agent_approved_handler(
                {**_evt(path={"agent_id": "agent_a"}), "headers": {"authorization": "Bearer user-tok"}},
                None,
            )
        h.assert_called_once_with("agent_a", "user-tok", status="approved")

    def test_list_agent_approved_handler_delegates_with_status(self):
        from handlers.tenant_approvals import list_agent_approved_handler
        with patch("handlers.tenant_approvals.handle_list_agent_approved", return_value=_OK) as h:
            list_agent_approved_handler(
                {**_evt(path={"agent_id": "agent_a"}, qs={"status": "pending"}),
                 "headers": {"authorization": "Bearer user-tok"}},
                None,
            )
        h.assert_called_once_with("agent_a", "user-tok", status="pending")

    def test_list_agent_approved_handler_missing_auth(self):
        from handlers.tenant_approvals import list_agent_approved_handler
        r = list_agent_approved_handler(_evt(headers={}, path={"agent_id": "a"}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# admin_jobs
# ---------------------------------------------------------------------------

class TestAdminJobsHandlers:
    def test_list_jobs_admin_handler_delegates(self):
        from handlers.admin_jobs import list_jobs_admin_handler
        with patch("handlers.admin_jobs.handle_list_jobs_admin", return_value=_OK) as h:
            list_jobs_admin_handler(_evt(qs={"tenant_id": "t1", "limit": "10"}), None)
        assert h.call_args[0][2] == "t1"  # tenant_id
        assert h.call_args[0][4] == 10    # limit

    def test_list_jobs_admin_handler_missing_auth(self):
        from handlers.admin_jobs import list_jobs_admin_handler
        r = list_jobs_admin_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_list_jobs_admin_handler_bad_limit_defaults(self):
        from handlers.admin_jobs import list_jobs_admin_handler
        with patch("handlers.admin_jobs.handle_list_jobs_admin", return_value=_OK) as h:
            list_jobs_admin_handler(_evt(qs={"tenant_id": "t1", "limit": "bad"}), None)
        assert h.call_args[0][4] == 20  # falls back to default


# ---------------------------------------------------------------------------
# agent_claim
# ---------------------------------------------------------------------------

class TestAgentClaimHandler:
    def test_delegates(self):
        from handlers.agent_claim import agent_claim_handler
        body = {"agent_id": "a", "install_token": "t", "machine_fingerprint": "f"}
        with patch("handlers.agent_claim.handle_agent_claim", return_value=_OK) as h:
            r = agent_claim_handler({"body": json.dumps(body)}, None)
        h.assert_called_once_with(body)
        assert r == _OK

    def test_invalid_json(self):
        from handlers.agent_claim import agent_claim_handler
        r = agent_claim_handler({"body": "bad"}, None)
        assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# agent_sync
# ---------------------------------------------------------------------------

class TestAgentSyncHandler:
    def test_delegates(self):
        from handlers.agent_sync import agent_sync_handler
        body = {"agent_id": "a", "machine_fingerprint": "f"}
        with patch("handlers.agent_sync.handle_agent_sync", return_value=_OK) as h:
            agent_sync_handler(_evt(body=body), None)
        h.assert_called_once_with(body, ADMIN)

    def test_missing_auth(self):
        from handlers.agent_sync import agent_sync_handler
        r = agent_sync_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_invalid_json(self):
        from handlers.agent_sync import agent_sync_handler
        evt = _evt(); evt["body"] = "bad"
        r = agent_sync_handler(evt, None)
        assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# agent_job_result
# ---------------------------------------------------------------------------

class TestAgentJobResultHandler:
    def test_delegates(self):
        from handlers.agent_job_result import agent_job_result_handler
        body = {"agent_id": "a", "machine_fingerprint": "f", "status": "SUCCEEDED"}
        evt = {**_evt(body=body, path={"job_id": "job_1"})}
        with patch("handlers.agent_job_result.handle_agent_job_result", return_value=_OK) as h:
            agent_job_result_handler(evt, None)
        h.assert_called_once_with("job_1", body, ADMIN)

    def test_missing_auth(self):
        from handlers.agent_job_result import agent_job_result_handler
        r = agent_job_result_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_invalid_json(self):
        from handlers.agent_job_result import agent_job_result_handler
        evt = _evt(path={"job_id": "j1"}); evt["body"] = "bad"
        r = agent_job_result_handler(evt, None)
        assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# agent_rotate_token
# ---------------------------------------------------------------------------

class TestAgentRotateTokenHandler:
    def test_delegates(self):
        from handlers.agent_rotate_token import agent_rotate_token_handler
        body = {"agent_id": "a", "machine_fingerprint": "f"}
        with patch("handlers.agent_rotate_token.handle_agent_rotate_token", return_value=_OK) as h:
            agent_rotate_token_handler(_evt(body=body), None)
        h.assert_called_once_with(body, ADMIN)

    def test_missing_auth(self):
        from handlers.agent_rotate_token import agent_rotate_token_handler
        r = agent_rotate_token_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_invalid_json(self):
        from handlers.agent_rotate_token import agent_rotate_token_handler
        evt = _evt(); evt["body"] = "bad"
        r = agent_rotate_token_handler(evt, None)
        assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------

class TestCreateJobHandler:
    def test_delegates(self):
        from handlers.create_job import create_job_handler
        body = {"agent_id": "a", "command": "ls"}
        with patch("handlers.create_job.handle_create_job", return_value=_OK) as h:
            create_job_handler(_evt(body=body), None)
        h.assert_called_once_with(body, ADMIN)

    def test_missing_auth(self):
        from handlers.create_job import create_job_handler
        r = create_job_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_invalid_json(self):
        from handlers.create_job import create_job_handler
        evt = _evt(); evt["body"] = "bad"
        r = create_job_handler(evt, None)
        assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# get_agent
# ---------------------------------------------------------------------------

class TestGetAgentHandler:
    def test_delegates(self):
        from handlers.get_agent import get_agent_handler
        with patch("handlers.get_agent.handle_get_agent", return_value=_OK) as h:
            get_agent_handler(_evt(path={"agent_id": "agent_a"}), None)
        h.assert_called_once_with("agent_a", ADMIN)

    def test_missing_auth(self):
        from handlers.get_agent import get_agent_handler
        r = get_agent_handler(_evt(headers={}, path={"agent_id": "a"}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# get_job
# ---------------------------------------------------------------------------

class TestGetJobHandler:
    def test_delegates(self):
        from handlers.get_job import get_job_handler
        with patch("handlers.get_job.handle_get_job", return_value=_OK) as h:
            get_job_handler(_evt(path={"job_id": "job_1"}), None)
        h.assert_called_once_with("job_1", ADMIN)

    def test_missing_auth(self):
        from handlers.get_job import get_job_handler
        r = get_job_handler(_evt(headers={}, path={"job_id": "j1"}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# list_agents
# ---------------------------------------------------------------------------

class TestListAgentsHandler:
    def test_delegates_with_tag(self):
        from handlers.list_agents import list_agents_handler
        with patch("handlers.list_agents.handle_list_agents", return_value=_OK) as h:
            list_agents_handler(_evt(qs={"tag": "env:prod"}), None)
        h.assert_called_once_with(ADMIN, "env:prod")

    def test_delegates_without_tag(self):
        from handlers.list_agents import list_agents_handler
        with patch("handlers.list_agents.handle_list_agents", return_value=_OK) as h:
            list_agents_handler(_evt(), None)
        h.assert_called_once_with(ADMIN, None)

    def test_missing_auth(self):
        from handlers.list_agents import list_agents_handler
        r = list_agents_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------

class TestListJobsHandler:
    def test_delegates(self):
        from handlers.list_jobs import list_jobs_handler
        with patch("handlers.list_jobs.handle_list_jobs", return_value=_OK) as h:
            list_jobs_handler(_evt(qs={"agent_id": "agent_a", "limit": "5"}), None)
        assert h.call_args[0][1] == "agent_a"
        assert h.call_args[0][2] == 5

    def test_missing_auth(self):
        from handlers.list_jobs import list_jobs_handler
        r = list_jobs_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_bad_limit_defaults_to_20(self):
        from handlers.list_jobs import list_jobs_handler
        with patch("handlers.list_jobs.handle_list_jobs", return_value=_OK) as h:
            list_jobs_handler(_evt(qs={"limit": "not-a-number"}), None)
        assert h.call_args[0][2] == 20


# ---------------------------------------------------------------------------
# me
# ---------------------------------------------------------------------------

class TestMeHandler:
    def test_delegates(self):
        from handlers.me import me_handler
        with patch("handlers.me.handle_me", return_value=_OK) as h:
            me_handler(_evt(), None)
        h.assert_called_once_with(ADMIN)

    def test_missing_auth(self):
        from handlers.me import me_handler
        r = me_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------

class TestHeartbeatHandler:
    def test_delegates_and_returns_result(self):
        from handlers.heartbeat import heartbeat_handler
        result = {"marked_inactive": 2, "expired_jobs": 1}
        with patch("handlers.heartbeat.handle_heartbeat_check", return_value=result):
            r = heartbeat_handler({}, None)
        assert r == result
