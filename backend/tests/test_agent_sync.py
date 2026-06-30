import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, call, patch

from handlers.agent_sync import _audit_capability_changes, handle_agent_sync

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

    def test_missing_fingerprint(self):
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_ACTIVE):
            r = handle_agent_sync({}, "tok")
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

    def test_reactivation_writes_agent_recovered_audit(self):
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_INACTIVE), \
             patch("handlers.agent_sync.agents_repo") as ar, \
             patch("handlers.agent_sync.approvals_repo") as apr, \
             patch("handlers.agent_sync.jobs_repo") as jr, \
             patch("handlers.agent_sync.agent_history_repo"), \
             patch("handlers.agent_sync.audit") as audit:
            jr.get_pending_for_agent.return_value = []
            jr.set_running.return_value = True
            apr.list_by_agent.return_value = []
            handle_agent_sync(_VALID_BODY, "tok")
        # INACTIVE -> ACTIVE must be audited as agent.recovered.
        actions = [c.args[0] for c in audit.write.call_args_list]
        assert "agent.recovered" in actions

    def test_reactivation_records_agent_history(self):
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_INACTIVE), \
             patch("handlers.agent_sync.agents_repo"), \
             patch("handlers.agent_sync.approvals_repo") as apr, \
             patch("handlers.agent_sync.jobs_repo") as jr, \
             patch("handlers.agent_sync.agent_history_repo") as hist, \
             patch("handlers.agent_sync.audit"):
            jr.get_pending_for_agent.return_value = []
            jr.set_running.return_value = True
            apr.list_by_agent.return_value = []
            handle_agent_sync(_VALID_BODY, "tok")
        hist.create.assert_called_once()
        entry = hist.create.call_args[0][0]
        assert entry["from_status"] == "INACTIVE" and entry["to_status"] == "ACTIVE"
        assert entry["triggered_by"] == "heartbeat"

    def test_active_agent_sync_does_not_write_recovered_audit(self):
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_ACTIVE), \
             patch("handlers.agent_sync.agents_repo") as ar, \
             patch("handlers.agent_sync.approvals_repo") as apr, \
             patch("handlers.agent_sync.jobs_repo") as jr, \
             patch("handlers.agent_sync.audit") as audit:
            jr.get_pending_for_agent.return_value = []
            jr.set_running.return_value = True
            apr.list_by_agent.return_value = []
            handle_agent_sync(_VALID_BODY, "tok")
        actions = [c.args[0] for c in audit.write.call_args_list]
        assert "agent.recovered" not in actions

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

    def test_docker_detected_passed_to_update_heartbeat(self):
        body = {**_VALID_BODY, "docker_detected": True}
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_ACTIVE), \
             patch("handlers.agent_sync.agents_repo") as ar, \
             patch("handlers.agent_sync.approvals_repo"), \
             patch("handlers.agent_sync.jobs_repo") as jr, \
             patch("handlers.agent_sync.audit"):
            jr.get_pending_for_agent.return_value = []
            handle_agent_sync(body, "tok")
        _, kwargs = ar.update_heartbeat.call_args
        assert kwargs.get("docker_detected") is True

    def test_service_mgmt_detected_passed_to_update_heartbeat(self):
        body = {**_VALID_BODY, "service_mgmt_detected": False}
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_ACTIVE), \
             patch("handlers.agent_sync.agents_repo") as ar, \
             patch("handlers.agent_sync.approvals_repo"), \
             patch("handlers.agent_sync.jobs_repo") as jr, \
             patch("handlers.agent_sync.audit"):
            jr.get_pending_for_agent.return_value = []
            handle_agent_sync(body, "tok")
        _, kwargs = ar.update_heartbeat.call_args
        assert kwargs.get("service_mgmt_detected") is False

    def test_non_bool_docker_detected_treated_as_none(self):
        body = {**_VALID_BODY, "docker_detected": "yes"}
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_ACTIVE), \
             patch("handlers.agent_sync.agents_repo") as ar, \
             patch("handlers.agent_sync.approvals_repo"), \
             patch("handlers.agent_sync.jobs_repo") as jr, \
             patch("handlers.agent_sync.audit"):
            jr.get_pending_for_agent.return_value = []
            handle_agent_sync(body, "tok")
        _, kwargs = ar.update_heartbeat.call_args
        assert kwargs.get("docker_detected") is None

    def test_absent_capability_fields_sent_as_none(self):
        with patch("handlers.agent_sync._verify_agent_token", return_value=_AGENT_ACTIVE), \
             patch("handlers.agent_sync.agents_repo") as ar, \
             patch("handlers.agent_sync.approvals_repo"), \
             patch("handlers.agent_sync.jobs_repo") as jr, \
             patch("handlers.agent_sync.audit"):
            jr.get_pending_for_agent.return_value = []
            handle_agent_sync(_VALID_BODY, "tok")
        _, kwargs = ar.update_heartbeat.call_args
        assert kwargs.get("docker_detected") is None
        assert kwargs.get("service_mgmt_detected") is None


# ---------------------------------------------------------------------------
# _audit_capability_changes
# ---------------------------------------------------------------------------

_BASE_AGENT = {
    "agent_id": AGENT_ID,
    "tenant_id": "tenant_1",
    "hostname": "myhost",
    "grant_docker": False,
    "grant_service_mgmt": False,
    "docker_detected": None,
    "service_mgmt_detected": None,
}


class TestAuditCapabilityChanges:
    def _call(self, agent, docker=None, service=None):
        with patch("handlers.agent_sync.audit") as mock_audit:
            _audit_capability_changes(agent, docker, service)
        return mock_audit

    # --- None inputs are skipped entirely ---

    def test_none_docker_no_audit(self):
        mock = self._call(_BASE_AGENT, docker=None, service=None)
        mock.write.assert_not_called()

    # --- First detection: prev=None, detected=True, not granted → out-of-band ---

    def test_first_docker_detection_out_of_band_writes_audit(self):
        agent = {**_BASE_AGENT, "docker_detected": None, "grant_docker": False}
        mock = self._call(agent, docker=True)
        mock.write.assert_called_once()
        meta = mock.write.call_args[1]["metadata"]
        assert meta["capability"] == "docker"
        assert meta["detected"] is True
        assert meta["out_of_band"] is True

    def test_first_service_mgmt_detection_out_of_band_writes_audit(self):
        agent = {**_BASE_AGENT, "service_mgmt_detected": None, "grant_service_mgmt": False}
        mock = self._call(agent, service=True)
        mock.write.assert_called_once()
        meta = mock.write.call_args[1]["metadata"]
        assert meta["capability"] == "service_mgmt"
        assert meta["out_of_band"] is True

    # --- First detection: prev=None, detected=False → no audit (no change, not out-of-band) ---

    def test_first_detection_false_no_audit(self):
        agent = {**_BASE_AGENT, "docker_detected": None, "grant_docker": False}
        mock = self._call(agent, docker=False)
        mock.write.assert_not_called()

    # --- Detected=True, granted=True, prev=None → audit (first time, but NOT out-of-band) ---

    def test_first_detection_granted_writes_audit_not_out_of_band(self):
        agent = {**_BASE_AGENT, "docker_detected": None, "grant_docker": True}
        mock = self._call(agent, docker=True)
        mock.write.assert_called_once()
        meta = mock.write.call_args[1]["metadata"]
        assert meta["out_of_band"] is False

    # --- Value unchanged → no audit ---

    def test_unchanged_true_no_audit(self):
        agent = {**_BASE_AGENT, "docker_detected": True, "grant_docker": True}
        mock = self._call(agent, docker=True)
        mock.write.assert_not_called()

    def test_unchanged_false_no_audit(self):
        agent = {**_BASE_AGENT, "docker_detected": False, "grant_docker": False}
        mock = self._call(agent, docker=False)
        mock.write.assert_not_called()

    # --- Value changed True→False → audit ---

    def test_docker_reverted_writes_audit(self):
        agent = {**_BASE_AGENT, "docker_detected": True, "grant_docker": False}
        mock = self._call(agent, docker=False)
        mock.write.assert_called_once()
        meta = mock.write.call_args[1]["metadata"]
        assert meta["detected"] is False
        assert meta["previously_detected"] is True

    # --- Persistent out-of-band (prev=True, grant=False, detected=True) → no audit (unchanged) ---

    def test_persistent_out_of_band_no_repeated_audit(self):
        # State hasn't changed - docker was already detected out-of-band last heartbeat.
        # Must NOT re-audit on every subsequent heartbeat.
        agent = {**_BASE_AGENT, "docker_detected": True, "grant_docker": False}
        mock = self._call(agent, docker=True)
        mock.write.assert_not_called()

    # --- Both capabilities at once ---

    def test_both_capabilities_write_separate_audit_entries(self):
        agent = {**_BASE_AGENT}
        mock = self._call(agent, docker=True, service=True)
        assert mock.write.call_count == 2
        caps = {c[1]["metadata"]["capability"] for c in mock.write.call_args_list}
        assert caps == {"docker", "service_mgmt"}
