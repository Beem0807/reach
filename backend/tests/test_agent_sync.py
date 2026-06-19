import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from handlers.agent_sync import handle_agent_sync

AGENT_ID = "agent_a"
FP = "fp_abc123"

_AGENT_ACTIVE = {
    "agent_id": AGENT_ID, "status": "ACTIVE", "mode": "wild",
    "machine_fingerprint": FP, "active_until": 9999999999,
    "token_issued_at": datetime.now(tz=timezone.utc).isoformat(),
}
_AGENT_INACTIVE = {**_AGENT_ACTIVE, "status": "INACTIVE"}

_VALID_BODY = {"agent_id": AGENT_ID, "machine_fingerprint": FP, "agent_version": "0.1.0"}

_JOB_PENDING = {"job_id": "job_1", "command": "docker ps", "mode": "wild"}


class TestAgentSync:
    def _call(self, body=None, agent=_AGENT_ACTIVE, jobs=None):
        with patch("handlers.agent_sync._verify_agent_token", return_value=agent), \
             patch("handlers.agent_sync.agents_repo") as ar, \
             patch("handlers.agent_sync.approvals_repo") as apr, \
             patch("handlers.agent_sync.jobs_repo") as jr:
            jr.get_pending_for_agent.return_value = jobs or []
            jr.set_running.return_value = True
            apr.list_by_agent.return_value = []
            return handle_agent_sync(body or _VALID_BODY, "agent_tok")

    def test_missing_agent_id(self):
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_ACTIVE):
            r = handle_agent_sync({"machine_fingerprint": FP}, "tok")
        assert r["statusCode"] == 400

    def test_missing_fingerprint(self):
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_ACTIVE):
            r = handle_agent_sync({"agent_id": AGENT_ID}, "tok")
        assert r["statusCode"] == 400

    def test_unauthorized(self):
        with patch("handlers.agent_sync._verify_agent_token", return_value=None):
            r = handle_agent_sync(_VALID_BODY, "bad")
        assert r["statusCode"] == 401

    def test_fingerprint_mismatch(self):
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_ACTIVE), \
             patch("handlers.agent_sync.agents_repo"), \
             patch("handlers.agent_sync.approvals_repo"), \
             patch("handlers.agent_sync.jobs_repo"):
            r = handle_agent_sync({**_VALID_BODY, "machine_fingerprint": "wrong"}, "tok")
        assert r["statusCode"] == 403

    def test_expired_token_rejected(self):
        old_issued = (datetime.now(tz=timezone.utc) - timedelta(days=31)).isoformat()
        old_agent = {**_AGENT_ACTIVE, "token_issued_at": old_issued}
        r = self._call(agent=old_agent)
        assert r["statusCode"] == 403

    def test_returns_pending_jobs(self):
        r = self._call(jobs=[_JOB_PENDING])
        body = json.loads(r["body"])
        assert len(body["jobs"]) == 1
        assert body["jobs"][0]["job_id"] == "job_1"
        assert body["jobs"][0]["command"] == "docker ps"

    def test_returns_empty_jobs_when_idle(self):
        r = self._call(jobs=[])
        assert json.loads(r["body"])["jobs"] == []

    def test_next_poll_seconds_present(self):
        r = self._call()
        assert "next_poll_seconds" in json.loads(r["body"])

    def test_created_agent_not_allowed(self):
        created_agent = {**_AGENT_ACTIVE, "status": "CREATED"}
        r = self._call(agent=created_agent)
        assert r["statusCode"] == 403

    def test_inactive_agent_reactivated(self):
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_INACTIVE), \
             patch("handlers.agent_sync.agents_repo") as ar, \
             patch("handlers.agent_sync.approvals_repo") as apr, \
             patch("handlers.agent_sync.jobs_repo") as jr:
            jr.get_pending_for_agent.return_value = []
            jr.set_running.return_value = True
            apr.list_by_agent.return_value = []
            handle_agent_sync(_VALID_BODY, "tok")
        ar.update_heartbeat.assert_called_once()
        _, kwargs = ar.update_heartbeat.call_args
        assert kwargs.get("reactivate") is True or ar.update_heartbeat.call_args[0][1] is True

    def test_is_write_included_in_dispatched_job(self):
        job_with_write = {**_JOB_PENDING, "is_write": True}
        r = self._call(jobs=[job_with_write])
        body = json.loads(r["body"])
        assert body["jobs"][0]["is_write"] is True

    def test_is_write_defaults_false_when_absent(self):
        r = self._call(jobs=[_JOB_PENDING])
        body = json.loads(r["body"])
        assert body["jobs"][0]["is_write"] is False

    def test_approved_mode_includes_approved_commands_per_job(self):
        approved_agent = {**_AGENT_ACTIVE, "mode": "approved"}
        approved_records = [
            {"command": "docker ps", "status": "approved"},
            {"command": "git status", "status": "approved"},
        ]
        job_approved_mode = {**_JOB_PENDING, "mode": "approved"}
        with patch("handlers.agent_sync._verify_agent_token", return_value=approved_agent), \
             patch("handlers.agent_sync.agents_repo"), \
             patch("handlers.agent_sync.approvals_repo") as apr, \
             patch("handlers.agent_sync.jobs_repo") as jr:
            jr.get_pending_for_agent.return_value = [job_approved_mode]
            jr.set_running.return_value = True
            apr.list_by_agent.return_value = approved_records
            r = handle_agent_sync(_VALID_BODY, "tok")
        body = json.loads(r["body"])
        assert set(body["jobs"][0]["approved_commands"]) == {"docker ps", "git status"}
        apr.list_by_agent.assert_called_once_with(AGENT_ID, status="approved")

    def test_non_approved_mode_sends_empty_approved_commands(self):
        r = self._call(jobs=[_JOB_PENDING])  # default mode is "wild"
        body = json.loads(r["body"])
        assert body["jobs"][0]["approved_commands"] == []

    def test_rotate_token_signal_when_flag_set(self):
        agent_with_flag = {**_AGENT_ACTIVE, "rotation_requested": True}
        r = self._call(agent=agent_with_flag)
        assert json.loads(r["body"])["rotate_token"] is True

    def test_no_rotate_token_signal_when_flag_absent(self):
        r = self._call()
        assert "rotate_token" not in json.loads(r["body"])

    def test_no_rotate_token_signal_when_flag_false(self):
        agent_no_flag = {**_AGENT_ACTIVE, "rotation_requested": False}
        r = self._call(agent=agent_no_flag)
        assert "rotate_token" not in json.loads(r["body"])
