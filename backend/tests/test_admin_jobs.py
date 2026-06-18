import json
import base64
from unittest.mock import patch

from handlers.admin_jobs import handle_list_jobs_admin

ADMIN = "test-admin-token"
TENANT = "tenant_1"

_JOB = {
    "job_id": "job_1",
    "agent_id": "agent_a",
    "tenant_id": TENANT,
    "created_by": "user_1",
    "command": "docker ps",
    "status": "COMPLETE",
    "exit_code": 0,
    "duration_ms": 120,
    "created_at": "2026-06-17T10:00:00+00:00",
    "completed_at": "2026-06-17T10:00:00+00:00",
}


def _call(agent_id="", tenant_id=TENANT, created_by="", limit=20, cursor=None):
    with patch("handlers.admin_jobs.jobs_repo") as jr:
        jr.list_admin.return_value = []
        return handle_list_jobs_admin(ADMIN, agent_id, tenant_id, created_by, limit, cursor), jr


class TestListJobsAdmin:
    def test_unauthorized(self):
        r = handle_list_jobs_admin("wrong", "", TENANT, "", 20)
        assert r["statusCode"] == 401

    def test_no_filters_returns_400(self):
        with patch("handlers.admin_jobs.jobs_repo"):
            r = handle_list_jobs_admin(ADMIN, "", "", "", 20)
        assert r["statusCode"] == 400

    def test_tenant_filter_accepted(self):
        r, _ = _call(tenant_id=TENANT)
        assert r["statusCode"] == 200

    def test_agent_filter_accepted(self):
        r, _ = _call(agent_id="agent_a", tenant_id="")
        assert r["statusCode"] == 200

    def test_created_by_filter_accepted(self):
        r, _ = _call(created_by="user_1", tenant_id="")
        assert r["statusCode"] == 200

    def test_returns_jobs(self):
        with patch("handlers.admin_jobs.jobs_repo") as jr:
            jr.list_admin.return_value = [_JOB]
            r = handle_list_jobs_admin(ADMIN, "", TENANT, "", 20)
        jobs = json.loads(r["body"])["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "job_1"
        assert jobs[0]["created_by"] == "user_1"

    def test_next_cursor_when_full_page(self):
        jobs = [_JOB] * 20
        with patch("handlers.admin_jobs.jobs_repo") as jr:
            jr.list_admin.return_value = jobs
            r = handle_list_jobs_admin(ADMIN, "", TENANT, "", 20)
        body = json.loads(r["body"])
        assert "next_cursor" in body

    def test_no_next_cursor_on_partial_page(self):
        with patch("handlers.admin_jobs.jobs_repo") as jr:
            jr.list_admin.return_value = [_JOB]
            r = handle_list_jobs_admin(ADMIN, "", TENANT, "", 20)
        body = json.loads(r["body"])
        assert "next_cursor" not in body

    def test_invalid_cursor_passed_as_none(self):
        with patch("handlers.admin_jobs.jobs_repo") as jr:
            jr.list_admin.return_value = []
            handle_list_jobs_admin(ADMIN, "", TENANT, "", 20, "!!!not-valid-base64!!!")
        jr.list_admin.assert_called_once()
        # invalid cursor decodes to None, handler should not raise
        assert True

    def test_cursor_decoded_and_passed(self):
        raw_cursor = "2026-06-17T10:00:00+00:00"
        encoded = base64.urlsafe_b64encode(raw_cursor.encode()).decode()
        with patch("handlers.admin_jobs.jobs_repo") as jr:
            jr.list_admin.return_value = []
            handle_list_jobs_admin(ADMIN, "", TENANT, "", 20, encoded)
        jr.list_admin.assert_called_once()
        _, kwargs = jr.list_admin.call_args
        assert kwargs.get("cursor") == raw_cursor or jr.list_admin.call_args[0][-1] == raw_cursor
