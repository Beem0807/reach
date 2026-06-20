import json
import base64
from unittest.mock import patch

from handlers.create_job import handle_create_job
from handlers.get_job import handle_get_job
from handlers.list_jobs import handle_list_jobs

TENANT = "tenant_1"
USER = {"user_id": "user_1", "tenant_id": TENANT}
AGENT_ID = "agent_a"

_AGENT_ACTIVE = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE", "mode": "wild"}
_AGENT_INACTIVE = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "INACTIVE", "mode": "wild"}
_AGENT_READONLY = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE", "mode": "readonly"}
_AGENT_APPROVED = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE", "mode": "approved"}
_APPROVED_COMMANDS = [{"approval_id": "appr_1", "command": "docker ps", "status": "approved"}]

_JOB = {
    "job_id": "job_1", "agent_id": AGENT_ID, "tenant_id": TENANT,
    "created_by": "user_1", "command": "docker ps",
    "status": "SUCCEEDED", "exit_code": 0, "stdout": "output",
    "stderr": "", "duration_ms": 100,
    "created_at": "2026-06-17T10:00:00+00:00",
    "started_at": "2026-06-17T10:00:00+00:00",
    "completed_at": "2026-06-17T10:00:01+00:00",
    "expires_at": 9999999999,
}


# ---------------------------------------------------------------------------
# handle_create_job
# ---------------------------------------------------------------------------

class TestCreateJob:
    def _call(self, body, agent=_AGENT_ACTIVE, user=USER):
        with patch("handlers.create_job._verify_tenant_token", return_value=user), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.jobs_repo"):
            ar.get.return_value = agent
            return handle_create_job(body, "tok")

    def test_unauthorized(self):
        with patch("handlers.create_job._verify_tenant_token", return_value=None):
            r = handle_create_job({"agent_id": AGENT_ID, "command": "ls"}, "bad")
        assert r["statusCode"] == 401

    def test_missing_agent_id(self):
        r = self._call({"command": "ls"})
        assert r["statusCode"] == 400

    def test_missing_command(self):
        r = self._call({"agent_id": AGENT_ID})
        assert r["statusCode"] == 400

    def test_command_too_long(self):
        r = self._call({"agent_id": AGENT_ID, "command": "x" * 4097})
        assert r["statusCode"] == 400

    def test_blocked_command_rejected(self):
        r = self._call({"agent_id": AGENT_ID, "command": "rm -rf /"})
        assert r["statusCode"] == 403

    def test_agent_not_found(self):
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.jobs_repo"):
            ar.get.return_value = None
            r = handle_create_job({"agent_id": AGENT_ID, "command": "ls"}, "tok")
        assert r["statusCode"] == 404

    def test_agent_not_active(self):
        r = self._call({"agent_id": AGENT_ID, "command": "ls"}, agent=_AGENT_INACTIVE)
        assert r["statusCode"] == 409

    def test_readonly_mode_blocks_write(self):
        r = self._call({"agent_id": AGENT_ID, "command": "rm file.txt"}, agent=_AGENT_READONLY)
        assert r["statusCode"] == 403

    def test_readonly_mode_allows_read(self):
        r = self._call({"agent_id": AGENT_ID, "command": "docker ps"}, agent=_AGENT_READONLY)
        assert r["statusCode"] == 201

    def test_approved_mode_queues_write(self):
        # Server queues in approved mode regardless; agent enforces via Landlock.
        r = self._call({"agent_id": AGENT_ID, "command": "docker stop myapp"}, agent=_AGENT_APPROVED)
        assert r["statusCode"] == 201

    def test_approved_mode_queues_read(self):
        r = self._call({"agent_id": AGENT_ID, "command": "ls -la"}, agent=_AGENT_APPROVED)
        assert r["statusCode"] == 201

    def test_creates_job(self):
        r = self._call({"agent_id": AGENT_ID, "command": "ls"})
        assert r["statusCode"] == 201
        body = json.loads(r["body"])
        assert body["job_id"].startswith("job_")
        assert body["status"] == "PENDING"

    def test_no_access_returns_404(self):
        restricted_user = {**USER, "allowed_agent_ids": ["agent_other"]}
        r = self._call({"agent_id": AGENT_ID, "command": "ls"}, user=restricted_user)
        assert r["statusCode"] == 404

    def test_write_command_annotated_is_write_true(self):
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.jobs_repo") as jr:
            ar.get.return_value = _AGENT_ACTIVE
            handle_create_job({"agent_id": AGENT_ID, "command": "rm -rf /tmp/x"}, "tok")
        stored = jr.create.call_args[0][0]
        assert stored["is_write"] is True

    def test_read_command_annotated_is_write_false(self):
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.jobs_repo") as jr:
            ar.get.return_value = _AGENT_ACTIVE
            handle_create_job({"agent_id": AGENT_ID, "command": "docker ps"}, "tok")
        stored = jr.create.call_args[0][0]
        assert stored["is_write"] is False


# ---------------------------------------------------------------------------
# handle_get_job
# ---------------------------------------------------------------------------

class TestGetJob:
    def _call(self, job=_JOB, user=USER, agent=_AGENT_ACTIVE):
        with patch("handlers.get_job._verify_tenant_token", return_value=user), \
             patch("handlers.get_job.jobs_repo") as jr, \
             patch("handlers.get_job.agents_repo") as agr:
            jr.get.return_value = job
            agr.get.return_value = agent
            return handle_get_job("job_1", "tok")

    def test_unauthorized(self):
        with patch("handlers.get_job._verify_tenant_token", return_value=None):
            r = handle_get_job("job_1", "bad")
        assert r["statusCode"] == 401

    def test_job_not_found(self):
        with patch("handlers.get_job._verify_tenant_token", return_value=USER), \
             patch("handlers.get_job.jobs_repo") as jr:
            jr.get.return_value = None
            r = handle_get_job("job_1", "tok")
        assert r["statusCode"] == 404

    def test_wrong_tenant_returns_404(self):
        wrong_tenant_job = {**_JOB, "tenant_id": "other_tenant"}
        r = self._call(job=wrong_tenant_job)
        assert r["statusCode"] == 404

    def test_inaccessible_agent_returns_404(self):
        restricted_user = {**USER, "allowed_agent_ids": ["agent_other"]}
        r = self._call(user=restricted_user)
        assert r["statusCode"] == 404

    def test_agent_not_found_returns_404(self):
        r = self._call(agent=None)
        assert r["statusCode"] == 404

    def test_returns_job_fields(self):
        r = self._call()
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["job_id"] == "job_1"
        assert body["command"] == "docker ps"
        assert body["status"] == "SUCCEEDED"
        assert body["stdout"] == "output"

    def test_pending_expired_job_marked_expired(self):
        expired_job = {**_JOB, "status": "PENDING", "expires_at": 1}  # long past
        with patch("handlers.get_job._verify_tenant_token", return_value=USER), \
             patch("handlers.get_job.jobs_repo") as jr, \
             patch("handlers.get_job.agents_repo") as agr:
            jr.get.return_value = expired_job
            agr.get.return_value = _AGENT_ACTIVE
            r = handle_get_job("job_1", "tok")
        assert json.loads(r["body"])["status"] == "EXPIRED"
        jr.mark_expired.assert_called_once_with("job_1")


# ---------------------------------------------------------------------------
# handle_list_jobs
# ---------------------------------------------------------------------------

class TestListJobs:
    def _call(self, jobs=None, agent_id=None, limit=20, cursor=None, user=USER, agent=_AGENT_ACTIVE):
        with patch("handlers.list_jobs._verify_tenant_token", return_value=user), \
             patch("handlers.list_jobs.jobs_repo") as jr, \
             patch("handlers.list_jobs.agents_repo") as agr:
            jr.list_by_tenant.return_value = jobs or []
            agr.get.return_value = agent
            return handle_list_jobs("tok", agent_id, limit, cursor)

    def test_unauthorized(self):
        with patch("handlers.list_jobs._verify_tenant_token", return_value=None):
            r = handle_list_jobs("bad", None, 20)
        assert r["statusCode"] == 401

    def test_returns_empty(self):
        r = self._call()
        assert json.loads(r["body"])["jobs"] == []

    def test_returns_jobs(self):
        r = self._call([_JOB])
        jobs = json.loads(r["body"])["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "job_1"

    def test_no_next_cursor_on_partial_page(self):
        r = self._call([_JOB], limit=20)
        assert "next_cursor" not in json.loads(r["body"])

    def test_next_cursor_when_full_page(self):
        r = self._call([_JOB], limit=1)
        body = json.loads(r["body"])
        assert "next_cursor" in body
        decoded = base64.urlsafe_b64decode(body["next_cursor"]).decode()
        assert decoded == _JOB["created_at"]

    def test_invalid_cursor_does_not_raise(self):
        with patch("handlers.list_jobs._verify_tenant_token", return_value=USER), \
             patch("handlers.list_jobs.jobs_repo") as jr, \
             patch("handlers.list_jobs.agents_repo") as agr:
            jr.list_by_tenant.return_value = []
            agr.get.return_value = _AGENT_ACTIVE
            r = handle_list_jobs("tok", None, 20, "!!!bad-cursor!!!")
        assert r["statusCode"] == 200

    def test_passes_agent_filter(self):
        with patch("handlers.list_jobs._verify_tenant_token", return_value=USER), \
             patch("handlers.list_jobs.jobs_repo") as jr, \
             patch("handlers.list_jobs.agents_repo") as agr:
            jr.list_by_tenant.return_value = []
            agr.get.return_value = _AGENT_ACTIVE
            handle_list_jobs("tok", "agent_a", 20)
        call_args = jr.list_by_tenant.call_args[0]
        assert "agent_a" in call_args

    def test_agent_filter_inaccessible_returns_404(self):
        restricted_user = {**USER, "allowed_agent_ids": ["agent_other"]}
        r = self._call(agent_id=AGENT_ID, user=restricted_user)
        assert r["statusCode"] == 404

    def test_agent_filter_not_found_returns_404(self):
        r = self._call(agent_id=AGENT_ID, agent=None)
        assert r["statusCode"] == 404

    def test_enriches_agent_hostname_and_mode(self):
        agent_with_host = {**_AGENT_ACTIVE, "hostname": "prod-01.local", "mode": "readonly"}
        r = self._call([_JOB], agent=agent_with_host)
        jobs = json.loads(r["body"])["jobs"]
        assert jobs[0]["agent_hostname"] == "prod-01.local"
        assert jobs[0]["agent_mode"] == "readonly"

    def test_agent_hostname_none_when_not_set(self):
        r = self._call([_JOB])  # _AGENT_ACTIVE has no hostname field
        jobs = json.loads(r["body"])["jobs"]
        assert jobs[0]["agent_hostname"] is None
        assert jobs[0]["agent_mode"] == "wild"

    def test_list_includes_stdout_and_stderr(self):
        r = self._call([_JOB])
        jobs = json.loads(r["body"])["jobs"]
        assert jobs[0]["stdout"] == "output"
        assert jobs[0]["stderr"] == ""

    def test_no_agent_filter_excludes_inaccessible_jobs(self):
        job_other = {**_JOB, "job_id": "job_2", "agent_id": "agent_other"}
        restricted_user = {**USER, "allowed_agent_ids": [AGENT_ID]}
        def fake_get(aid):
            if aid == AGENT_ID:
                return _AGENT_ACTIVE
            return {"agent_id": aid, "tenant_id": TENANT}
        with patch("handlers.list_jobs._verify_tenant_token", return_value=restricted_user), \
             patch("handlers.list_jobs.jobs_repo") as jr, \
             patch("handlers.list_jobs.agents_repo") as agr:
            jr.list_by_tenant.return_value = [_JOB, job_other]
            agr.get.side_effect = fake_get
            r = handle_list_jobs("tok", None, 20)
        jobs = json.loads(r["body"])["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "job_1"
