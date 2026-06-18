import json
from unittest.mock import patch

from handlers.agent_job_result import handle_agent_job_result

AGENT_ID = "agent_a"
JOB_ID = "job_1"
FP = "fp_abc123"

_AGENT = {"agent_id": AGENT_ID, "machine_fingerprint": FP}
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
             patch("handlers.agent_job_result.jobs_repo") as jr:
            jr.get.return_value = job
            return handle_agent_job_result(JOB_ID, body or _VALID_BODY, "tok")

    def test_invalid_status(self):
        r = self._call({**_VALID_BODY, "status": "DONE"})
        assert r["statusCode"] == 400

    def test_missing_agent_id(self):
        r = self._call({**_VALID_BODY, "agent_id": ""})
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
             patch("handlers.agent_job_result.jobs_repo") as jr:
            jr.get.return_value = _JOB_RUNNING
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
             patch("handlers.agent_job_result.jobs_repo") as jr:
            jr.get.return_value = _JOB_RUNNING
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
             patch("handlers.agent_job_result.jobs_repo") as jr:
            jr.get.return_value = _JOB_RUNNING
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "stderr": large_stderr}, "tok")
        stored = jr.set_result.call_args[0][1]["stderr"]
        assert "[TRUNCATED]" in stored
        assert len(stored.encode()) <= 50_100

    def test_rejected_status_accepted(self):
        r = self._call({**_VALID_BODY, "status": "REJECTED"})
        assert r["statusCode"] == 200
