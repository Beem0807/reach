import json
from unittest.mock import patch

from handlers.agent_job_result import handle_agent_job_result

AGENT_ID = "agent_a"
JOB_ID = "job_1"
FP = "fp_abc123"

_AGENT = {"agent_id": AGENT_ID, "machine_fingerprint": FP, "status": "ACTIVE"}
_JOB_RUNNING = {"job_id": JOB_ID, "agent_id": AGENT_ID, "status": "RUNNING"}
_JOB_PENDING = {"job_id": JOB_ID, "agent_id": AGENT_ID, "status": "PENDING"}

_VALID_BODY = {
    "agent_id": AGENT_ID,
    "machine_fingerprint": FP,
    "status": "SUCCEEDED",
    "exit_code": 0,
    "stdout": "hello\n",
    "stderr": "",
    "duration_ms": 42,
}


class TestAgentJobResult:
    def _call(self, body=None, agent=_AGENT, job=_JOB_RUNNING):
        with patch("handlers.agent_job_result._verify_agent_token", return_value=agent), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo"), \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = job
            ur.get.return_value = None
            return handle_agent_job_result(JOB_ID, body or _VALID_BODY, "tok")

    def test_invalid_status(self):
        r = self._call({**_VALID_BODY, "status": "DONE"})
        assert r["statusCode"] == 400

    def test_missing_fingerprint(self):
        r = self._call({**_VALID_BODY, "machine_fingerprint": ""})
        assert r["statusCode"] == 400

    def test_unauthorized(self):
        with patch("handlers.agent_job_result._verify_agent_token", return_value=None):
            r = handle_agent_job_result(JOB_ID, _VALID_BODY, "bad")
        assert r["statusCode"] == 401

    def test_fingerprint_mismatch(self):
        r = self._call({**_VALID_BODY, "machine_fingerprint": "wrong"})
        assert r["statusCode"] == 403

    def test_revoked_agent_cannot_report(self):
        r = self._call(agent={**_AGENT, "status": "REVOKED"})
        assert r["statusCode"] == 403

    def test_job_not_found(self):
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr:
            jr.get.return_value = None
            r = handle_agent_job_result(JOB_ID, _VALID_BODY, "tok")
        assert r["statusCode"] == 404

    def test_job_belongs_to_wrong_agent(self):
        wrong_agent_job = {**_JOB_RUNNING, "agent_id": "agent_other"}
        r = self._call(job=wrong_agent_job)
        assert r["statusCode"] == 403

    def test_terminal_job_rejected(self):
        terminal_job = {**_JOB_RUNNING, "status": "SUCCEEDED"}
        r = self._call(job=terminal_job)
        assert r["statusCode"] == 409

    def test_success(self):
        r = self._call()
        assert r["statusCode"] == 200

    def test_saves_result(self):
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo"), \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = _JOB_RUNNING
            ur.get.return_value = None
            handle_agent_job_result(JOB_ID, _VALID_BODY, "tok")
        jr.set_result.assert_called_once()
        result_data = jr.set_result.call_args[0][1]
        assert result_data["status"] == "SUCCEEDED"
        assert result_data["exit_code"] == 0
        assert result_data["stdout"] == "hello\n"

    def test_stdout_truncated_when_too_large(self):
        large_stdout = "x" * 60_000
        r = self._call({**_VALID_BODY, "stdout": large_stdout})
        assert r["statusCode"] == 200
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo"), \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = _JOB_RUNNING
            ur.get.return_value = None
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "stdout": large_stdout}, "tok")
        stored = jr.set_result.call_args[0][1]["stdout"]
        assert "[TRUNCATED]" in stored
        assert len(stored.encode()) <= 50_100  # 50_000 + small margin for "[TRUNCATED]"

    def test_failed_status_accepted(self):
        r = self._call({**_VALID_BODY, "status": "FAILED", "exit_code": 1})
        assert r["statusCode"] == 200

    def test_stderr_truncated_when_too_large(self):
        large_stderr = "e" * 60_000
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo"), \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = _JOB_RUNNING
            ur.get.return_value = None
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "stderr": large_stderr}, "tok")
        stored = jr.set_result.call_args[0][1]["stderr"]
        assert "[TRUNCATED]" in stored
        assert len(stored.encode()) <= 50_100

    def test_rejected_status_accepted(self):
        r = self._call({**_VALID_BODY, "status": "REJECTED"})
        assert r["statusCode"] == 200

    def test_is_write_stored_on_job(self):
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo"), \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = _JOB_RUNNING
            ur.get.return_value = None
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "is_write": True}, "tok")
        stored = jr.set_result.call_args[0][1]
        assert stored["is_write"] is True

    def test_blocked_true_infers_is_write(self):
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo"), \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = _JOB_RUNNING
            ur.get.return_value = None
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "blocked": True}, "tok")
        stored = jr.set_result.call_args[0][1]
        assert stored["is_write"] is True

    def test_blocked_creates_approval_record(self):
        job_with_meta = {**_JOB_RUNNING, "tenant_id": "tenant_1", "command": "rm -rf /tmp/x", "created_by": "user_1"}
        user = {"user_id": "user_1", "name": "Alice"}
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = job_with_meta
            apr.exists_pending.return_value = False
            ur.get.return_value = user
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "blocked": True, "status": "FAILED"}, "tok")
        apr.create.assert_called_once()
        record = apr.create.call_args[0][0]
        assert record["status"] == "pending"
        assert record["command"] == "rm -rf /tmp/x"
        assert record["agent_id"] == AGENT_ID
        assert record["requested_by"] == "user_1"
        assert record["requester_name"] == "Alice"
        assert record["job_id"] == JOB_ID
        assert record["approval_id"].startswith("appr_")

    def test_blocked_skips_create_when_pending_exists(self):
        job_with_meta = {**_JOB_RUNNING, "tenant_id": "tenant_1", "command": "rm -rf /tmp/x", "created_by": "user_1"}
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo"):
            jr.get.return_value = job_with_meta
            apr.exists_pending.return_value = True
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "blocked": True, "status": "FAILED"}, "tok")
        apr.create.assert_not_called()

    def test_not_blocked_does_not_create_approval(self):
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = _JOB_RUNNING
            ur.get.return_value = None
            handle_agent_job_result(JOB_ID, _VALID_BODY, "tok")
        apr.create.assert_not_called()
