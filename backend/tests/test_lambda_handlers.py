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
        h.assert_called_once_with(ADMIN, q=None, limit=None, offset=0)
        assert r == _OK

    def test_list_tenants_handler_missing_auth(self):
        from handlers.admin_tenants import list_tenants_handler
        r = list_tenants_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_delete_tenant_handler_delegates(self):
        from handlers.admin_tenants import delete_tenant_handler
        with patch("handlers.admin_tenants.handle_delete_tenant", return_value=_OK) as h:
            r = delete_tenant_handler(_evt(path={"tenant_id": "tenant_1"}), None)
        h.assert_called_once_with("tenant_1", ADMIN)
        assert r == _OK

    def test_delete_tenant_handler_missing_auth(self):
        from handlers.admin_tenants import delete_tenant_handler
        r = delete_tenant_handler(_evt(headers={}, path={"tenant_id": "tenant_1"}), None)
        assert r["statusCode"] == 401

    def test_disable_tenant_handler_delegates(self):
        from handlers.admin_tenants import disable_tenant_handler
        with patch("handlers.admin_tenants.handle_disable_tenant", return_value=_OK) as h:
            disable_tenant_handler(_evt(path={"tenant_id": "tenant_1"}), None)
        h.assert_called_once_with("tenant_1", ADMIN)

    def test_disable_tenant_handler_missing_auth(self):
        from handlers.admin_tenants import disable_tenant_handler
        r = disable_tenant_handler(_evt(headers={}, path={"tenant_id": "tenant_1"}), None)
        assert r["statusCode"] == 401

    def test_enable_tenant_handler_delegates(self):
        from handlers.admin_tenants import enable_tenant_handler
        with patch("handlers.admin_tenants.handle_enable_tenant", return_value=_OK) as h:
            enable_tenant_handler(_evt(path={"tenant_id": "tenant_1"}), None)
        h.assert_called_once_with("tenant_1", ADMIN)

    def test_enable_tenant_handler_missing_auth(self):
        from handlers.admin_tenants import enable_tenant_handler
        r = enable_tenant_handler(_evt(headers={}, path={"tenant_id": "tenant_1"}), None)
        assert r["statusCode"] == 401

    def test_create_admin_user_handler_delegates(self):
        from handlers.admin_tenants import create_admin_user_handler
        with patch("handlers.admin_tenants.handle_create_tenant_admin_user", return_value=_OK) as h:
            create_admin_user_handler(_evt(body={"username": "alice"}, path={"tenant_id": "tenant_1"}), None)
        assert h.call_args[0][0] == "tenant_1"
        assert h.call_args[0][1] == {"username": "alice"}
        assert h.call_args[0][2] == ADMIN

    def test_create_admin_user_handler_missing_auth(self):
        from handlers.admin_tenants import create_admin_user_handler
        r = create_admin_user_handler(_evt(headers={}, path={"tenant_id": "tenant_1"}), None)
        assert r["statusCode"] == 401

    def test_create_admin_user_handler_invalid_json(self):
        from handlers.admin_tenants import create_admin_user_handler
        evt = _evt(path={"tenant_id": "tenant_1"}); evt["body"] = "bad"
        r = create_admin_user_handler(evt, None)
        assert r["statusCode"] == 400

    def test_platform_reset_password_handler_delegates(self):
        from handlers.admin_tenants import platform_reset_password_handler
        with patch("handlers.admin_tenants.handle_platform_reset_user_password", return_value=_OK) as h:
            platform_reset_password_handler(_evt(path={"tenant_id": "t1", "user_id": "u1"}), None)
        assert h.call_args[0][:2] == ("t1", "u1")

    def test_platform_reset_password_handler_missing_auth(self):
        from handlers.admin_tenants import platform_reset_password_handler
        r = platform_reset_password_handler(_evt(headers={}, path={"tenant_id": "t1", "user_id": "u1"}), None)
        assert r["statusCode"] == 401

    def test_platform_disable_user_handler_delegates(self):
        from handlers.admin_tenants import platform_disable_user_handler
        with patch("handlers.admin_tenants.handle_platform_disable_user", return_value=_OK) as h:
            platform_disable_user_handler(_evt(path={"tenant_id": "t1", "user_id": "u1"}), None)
        assert h.call_args[0][:2] == ("t1", "u1")

    def test_platform_disable_user_handler_missing_auth(self):
        from handlers.admin_tenants import platform_disable_user_handler
        r = platform_disable_user_handler(_evt(headers={}, path={"tenant_id": "t1", "user_id": "u1"}), None)
        assert r["statusCode"] == 401

    def test_platform_set_role_handler_delegates(self):
        from handlers.admin_tenants import platform_set_role_handler
        with patch("handlers.admin_tenants.handle_platform_set_user_role", return_value=_OK) as h:
            platform_set_role_handler(_evt(body={"role": "operator"}, path={"tenant_id": "t1", "user_id": "u1"}), None)
        assert h.call_args[0][:2] == ("t1", "u1")
        assert h.call_args[0][2] == {"role": "operator"}

    def test_platform_set_role_handler_missing_auth(self):
        from handlers.admin_tenants import platform_set_role_handler
        r = platform_set_role_handler(_evt(headers={}, path={"tenant_id": "t1", "user_id": "u1"}), None)
        assert r["statusCode"] == 401

    def test_platform_set_role_handler_invalid_json(self):
        from handlers.admin_tenants import platform_set_role_handler
        evt = _evt(path={"tenant_id": "t1", "user_id": "u1"}); evt["body"] = "bad"
        r = platform_set_role_handler(evt, None)
        assert r["statusCode"] == 400

    def test_platform_update_name_handler_delegates(self):
        from handlers.admin_tenants import platform_update_name_handler
        with patch("handlers.admin_tenants.handle_platform_update_user_name", return_value=_OK) as h:
            platform_update_name_handler(_evt(body={"name": "Alice"}, path={"tenant_id": "t1", "user_id": "u1"}), None)
        assert h.call_args[0][:2] == ("t1", "u1")
        assert h.call_args[0][2] == {"name": "Alice"}

    def test_platform_update_name_handler_missing_auth(self):
        from handlers.admin_tenants import platform_update_name_handler
        r = platform_update_name_handler(_evt(headers={}, path={"tenant_id": "t1", "user_id": "u1"}), None)
        assert r["statusCode"] == 401

    def test_platform_update_name_handler_invalid_json(self):
        from handlers.admin_tenants import platform_update_name_handler
        evt = _evt(path={"tenant_id": "t1", "user_id": "u1"}); evt["body"] = "bad"
        r = platform_update_name_handler(evt, None)
        assert r["statusCode"] == 400


# ---------------------------------------------------------------------------
# admin_users (list only)
# ---------------------------------------------------------------------------

class TestAdminUserHandlers:
    def test_list_users_handler_delegates(self):
        from handlers.admin_users import list_users_handler
        with patch("handlers.admin_users.handle_list_users", return_value=_OK) as h:
            list_users_handler(_evt(path={"tenant_id": "tenant_1"}), None)
        h.assert_called_once_with("tenant_1", ADMIN, q=None, limit=None, offset=0)

    def test_list_users_handler_missing_auth(self):
        from handlers.admin_users import list_users_handler
        r = list_users_handler(_evt(headers={}, path={"tenant_id": "tenant_1"}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# admin_agents (read-only list only)
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

    def test_list_all_approvals_handler_delegates(self):
        from handlers.tenant_approvals import list_all_approvals_handler
        with patch("handlers.tenant_approvals.handle_tenant_list_all_approvals", return_value=_OK) as h:
            list_all_approvals_handler({**_evt(qs={"status": "pending"}), "headers": {"authorization": "Bearer user-tok"}}, None)
        h.assert_called_once_with({"status": "pending"}, "user-tok")

    def test_list_all_approvals_handler_missing_auth(self):
        from handlers.tenant_approvals import list_all_approvals_handler
        r = list_all_approvals_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_pre_approve_handler_delegates(self):
        from handlers.tenant_approvals import pre_approve_handler
        with patch("handlers.tenant_approvals.handle_tenant_create_approval", return_value=_OK) as h:
            pre_approve_handler({**_evt(body={"agent_id": "a", "command": "ls"}), "headers": {"authorization": "Bearer user-tok"}}, None)
        assert h.call_args[0][0] == {"agent_id": "a", "command": "ls"}

    def test_pre_approve_handler_missing_auth(self):
        from handlers.tenant_approvals import pre_approve_handler
        r = pre_approve_handler(_evt(headers={}), None)
        assert r["statusCode"] == 401

    def test_pre_approve_handler_invalid_json(self):
        from handlers.tenant_approvals import pre_approve_handler
        evt = {**_evt(), "headers": {"authorization": "Bearer tok"}}; evt["body"] = "bad"
        r = pre_approve_handler(evt, None)
        assert r["statusCode"] == 400

    def test_review_approval_handler_delegates(self):
        from handlers.tenant_approvals import review_approval_handler
        with patch("handlers.tenant_approvals.handle_tenant_review_approval", return_value=_OK) as h:
            review_approval_handler(
                {**_evt(body={"duration": "8h"}, path={"approval_id": "appr_1", "action": "approve"}),
                 "headers": {"authorization": "Bearer user-tok"}},
                None,
            )
        h.assert_called_once_with("appr_1", "approve", "user-tok", {"duration": "8h"})

    def test_review_approval_handler_missing_auth(self):
        from handlers.tenant_approvals import review_approval_handler
        r = review_approval_handler(_evt(headers={}, path={"approval_id": "a", "action": "approve"}), None)
        assert r["statusCode"] == 401

    def test_delete_approval_handler_delegates(self):
        from handlers.tenant_approvals import delete_approval_handler
        with patch("handlers.tenant_approvals.handle_tenant_delete_approval", return_value=_OK) as h:
            delete_approval_handler({**_evt(path={"approval_id": "appr_1"}), "headers": {"authorization": "Bearer user-tok"}}, None)
        h.assert_called_once_with("appr_1", "user-tok")

    def test_delete_approval_handler_missing_auth(self):
        from handlers.tenant_approvals import delete_approval_handler
        r = delete_approval_handler(_evt(headers={}, path={"approval_id": "a"}), None)
        assert r["statusCode"] == 401


# ---------------------------------------------------------------------------
# tenant_agents
# ---------------------------------------------------------------------------

TENANT_TOK = "user-jwt-tok"
_TEVT_PATH = {"agent_id": "agent_a"}

class TestTenantAgentHandlers:
    def _evt_t(self, body=None, path=None, qs=None):
        return {
            "headers": {"authorization": f"Bearer {TENANT_TOK}", "host": "api.example.com"},
            "body": json.dumps(body) if body is not None else None,
            "pathParameters": path or {},
            "queryStringParameters": qs or {},
        }

    def test_create_agent_handler_delegates(self):
        from handlers.tenant_agents import create_tenant_agent_handler
        with patch("handlers.tenant_agents.handle_create_tenant_agent", return_value=_OK) as h:
            create_tenant_agent_handler(self._evt_t(body={"mode": "wild"}), None)
        assert h.call_args[0][0] == {"mode": "wild"}
        assert h.call_args[0][1] == TENANT_TOK

    def test_create_agent_handler_missing_auth(self):
        from handlers.tenant_agents import create_tenant_agent_handler
        r = create_tenant_agent_handler({**self._evt_t(), "headers": {}}, None)
        assert r["statusCode"] == 401

    def test_create_agent_handler_invalid_json(self):
        from handlers.tenant_agents import create_tenant_agent_handler
        evt = self._evt_t(); evt["body"] = "bad"
        r = create_tenant_agent_handler(evt, None)
        assert r["statusCode"] == 400

    def test_reissue_install_token_handler_delegates(self):
        from handlers.tenant_agents import reissue_tenant_install_token_handler
        with patch("handlers.tenant_agents.handle_reissue_tenant_install_token", return_value=_OK) as h:
            reissue_tenant_install_token_handler(self._evt_t(path=_TEVT_PATH), None)
        assert h.call_args[0][0] == "agent_a"
        assert h.call_args[0][2] == TENANT_TOK

    def test_reissue_install_token_handler_missing_auth(self):
        from handlers.tenant_agents import reissue_tenant_install_token_handler
        r = reissue_tenant_install_token_handler({**self._evt_t(path=_TEVT_PATH), "headers": {}}, None)
        assert r["statusCode"] == 401

    def test_reissue_install_token_handler_invalid_json(self):
        from handlers.tenant_agents import reissue_tenant_install_token_handler
        evt = self._evt_t(path=_TEVT_PATH); evt["body"] = "bad"
        r = reissue_tenant_install_token_handler(evt, None)
        assert r["statusCode"] == 400

    def test_revoke_agent_handler_delegates(self):
        from handlers.tenant_agents import revoke_tenant_agent_handler
        with patch("handlers.tenant_agents.handle_revoke_tenant_agent", return_value=_OK) as h:
            revoke_tenant_agent_handler(self._evt_t(path=_TEVT_PATH), None)
        h.assert_called_once_with("agent_a", TENANT_TOK)

    def test_revoke_agent_handler_missing_auth(self):
        from handlers.tenant_agents import revoke_tenant_agent_handler
        r = revoke_tenant_agent_handler({**self._evt_t(path=_TEVT_PATH), "headers": {}}, None)
        assert r["statusCode"] == 401

    def test_delete_agent_handler_delegates(self):
        from handlers.tenant_agents import delete_tenant_agent_handler
        with patch("handlers.tenant_agents.handle_delete_tenant_agent", return_value=_OK) as h:
            delete_tenant_agent_handler(self._evt_t(path=_TEVT_PATH), None)
        h.assert_called_once_with("agent_a", TENANT_TOK)

    def test_delete_agent_handler_missing_auth(self):
        from handlers.tenant_agents import delete_tenant_agent_handler
        r = delete_tenant_agent_handler({**self._evt_t(path=_TEVT_PATH), "headers": {}}, None)
        assert r["statusCode"] == 401

    def test_remove_agent_handler_delegates(self):
        from handlers.tenant_agents import remove_tenant_agent_handler
        with patch("handlers.tenant_agents.handle_remove_tenant_agent", return_value=_OK) as h:
            remove_tenant_agent_handler(self._evt_t(path=_TEVT_PATH), None)
        h.assert_called_once_with("agent_a", TENANT_TOK)

    def test_remove_agent_handler_missing_auth(self):
        from handlers.tenant_agents import remove_tenant_agent_handler
        r = remove_tenant_agent_handler({**self._evt_t(path=_TEVT_PATH), "headers": {}}, None)
        assert r["statusCode"] == 401

    def test_set_tags_handler_delegates(self):
        from handlers.tenant_agents import set_tenant_agent_tags_handler
        with patch("handlers.tenant_agents.handle_set_tenant_agent_tags", return_value=_OK) as h:
            set_tenant_agent_tags_handler(self._evt_t(body={"tags": ["env:prod"]}, path=_TEVT_PATH), None)
        assert h.call_args[0][0] == "agent_a"
        assert h.call_args[0][1] == {"tags": ["env:prod"]}

    def test_set_tags_handler_missing_auth(self):
        from handlers.tenant_agents import set_tenant_agent_tags_handler
        r = set_tenant_agent_tags_handler({**self._evt_t(path=_TEVT_PATH), "headers": {}}, None)
        assert r["statusCode"] == 401

    def test_set_tags_handler_invalid_json(self):
        from handlers.tenant_agents import set_tenant_agent_tags_handler
        evt = self._evt_t(path=_TEVT_PATH); evt["body"] = "bad"
        r = set_tenant_agent_tags_handler(evt, None)
        assert r["statusCode"] == 400

    def test_set_mode_handler_delegates(self):
        from handlers.tenant_agents import set_tenant_agent_mode_handler
        with patch("handlers.tenant_agents.handle_set_tenant_agent_mode", return_value=_OK) as h:
            set_tenant_agent_mode_handler(self._evt_t(body={"mode": "readonly"}, path=_TEVT_PATH), None)
        h.assert_called_once_with("agent_a", {"mode": "readonly"}, TENANT_TOK)

    def test_set_mode_handler_missing_auth(self):
        from handlers.tenant_agents import set_tenant_agent_mode_handler
        r = set_tenant_agent_mode_handler({**self._evt_t(path=_TEVT_PATH), "headers": {}}, None)
        assert r["statusCode"] == 401

    def test_set_mode_handler_invalid_json(self):
        from handlers.tenant_agents import set_tenant_agent_mode_handler
        evt = self._evt_t(path=_TEVT_PATH); evt["body"] = "bad"
        r = set_tenant_agent_mode_handler(evt, None)
        assert r["statusCode"] == 400


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
        h.assert_called_once_with(body, ADMIN, "")

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
        h.assert_called_once_with(ADMIN, "env:prod", q=None, mode=None, access=None,
                                  agent_type=None, fleet=None, limit=None, offset=0)

    def test_delegates_without_tag(self):
        from handlers.list_agents import list_agents_handler
        with patch("handlers.list_agents.handle_list_agents", return_value=_OK) as h:
            list_agents_handler(_evt(), None)
        h.assert_called_once_with(ADMIN, None, q=None, mode=None, access=None,
                                  agent_type=None, fleet=None, limit=None, offset=0)

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
        result = {"marked_inactive": 2, "reaped_members": 0, "expired_jobs": 1, "expired_approvals": 0, "deleted_approvals": 0, "deleted_jobs": 0, "deleted_runs": 0, "deleted_audit_logs": 0, "deleted_agent_history": 0}
        with patch("handlers.heartbeat.handle_heartbeat_check", return_value=result):
            r = heartbeat_handler({}, None)
        assert r == result
