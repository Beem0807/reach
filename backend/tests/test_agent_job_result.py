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

    def test_backend_cap_sets_truncated_flag(self):
        # Agent under-reports (no flag), but server has to cut -> flag forced True.
        large = "x" * 60_000
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo"), \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = _JOB_RUNNING
            ur.get.return_value = None
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "stdout": large}, "tok")
        stored = jr.set_result.call_args[0][1]
        assert stored["stdout_truncated"] is True
        assert stored["stderr_truncated"] is False

    def test_agent_reported_truncation_persisted(self):
        # Agent already capped (output fits under 50KB) and reports the flag -> preserved.
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo"), \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = _JOB_RUNNING
            ur.get.return_value = None
            handle_agent_job_result(
                JOB_ID,
                {**_VALID_BODY, "stdout": "capped\n[TRUNCATED]", "stdout_truncated": True},
                "tok",
            )
        stored = jr.set_result.call_args[0][1]
        assert stored["stdout_truncated"] is True

    def test_no_truncation_flag_false(self):
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo"), \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = _JOB_RUNNING
            ur.get.return_value = None
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "stdout": "hi\n"}, "tok")
        stored = jr.set_result.call_args[0][1]
        assert stored["stdout_truncated"] is False
        assert stored["stderr_truncated"] is False

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

    def test_blocked_approval_is_structured_from_command(self):
        # Block-raised approvals are structured {bin, args[]} like the rest of the model.
        job_with_meta = {**_JOB_RUNNING, "tenant_id": "tenant_1", "command": "rm -rf /tmp/x", "created_by": "user_1"}
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = job_with_meta
            apr.exists_pending.return_value = False
            ur.get.return_value = {"user_id": "user_1", "name": "Alice"}
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "blocked": True, "status": "FAILED"}, "tok")
        record = apr.create.call_args[0][0]
        assert record["host_rule"] == {"bin": "rm", "args": ["-rf", "/tmp/x"]}
        assert record["command"] == "rm -rf /tmp/x"   # canonical display form

    def test_blocked_approval_prefers_dispatched_argv(self):
        # When the blocked job carries an argv, the rule is built from it directly.
        job_with_argv = {**_JOB_RUNNING, "tenant_id": "tenant_1", "command": "systemctl restart nginx",
                         "argv": ["systemctl", "restart", "nginx"], "created_by": "user_1"}
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = job_with_argv
            apr.exists_pending.return_value = False
            ur.get.return_value = {"user_id": "user_1", "name": "Alice"}
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "blocked": True, "status": "FAILED"}, "tok")
        record = apr.create.call_args[0][0]
        assert record["host_rule"] == {"bin": "systemctl", "args": ["restart", "nginx"]}

    def test_blocked_creates_fleet_scoped_approval_for_member(self):
        # A blocked write on a fleet member raises a fleet-scoped pending request.
        member = {**_AGENT, "fleet_id": "fleet_a"}
        job_with_meta = {**_JOB_RUNNING, "tenant_id": "tenant_1", "command": "rm -rf /tmp/x", "created_by": "user_1"}
        with patch("handlers.agent_job_result._verify_agent_token", return_value=member), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = job_with_meta
            apr.exists_pending_fleet.return_value = False
            ur.get.return_value = {"user_id": "user_1", "name": "Alice"}
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "blocked": True, "status": "FAILED"}, "tok")
        apr.exists_pending_fleet.assert_called_once_with("fleet_a", "rm -rf /tmp/x")
        apr.exists_pending.assert_not_called()
        record = apr.create.call_args[0][0]
        assert record["fleet_id"] == "fleet_a" and record["agent_id"] is None

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

    def test_blocked_skips_create_when_rule_already_approved(self):
        # A transient block of an already-approved write (e.g. agent hadn't synced the
        # grant yet) must NOT raise a pending - otherwise the same command lands in both
        # the Approved and Pending lists.
        job = {**_JOB_RUNNING, "tenant_id": "tenant_1", "command": "rm -rf /tmp/x",
               "argv": ["rm", "-rf", "/tmp/x"], "created_by": "user_1"}
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo"):
            jr.get.return_value = job
            apr.exists_pending.return_value = False
            apr.list_by_agent.return_value = [
                {"host_rule": {"bin": "rm", "args": ["-rf", "/tmp/x"]}, "command": "rm -rf /tmp/x", "status": "approved"},
            ]
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "blocked": True, "status": "FAILED"}, "tok")
        apr.create.assert_not_called()

    def test_blocked_skips_create_when_derived_rule_already_approved(self):
        # Guard: the rule derived from the blocked command already matches an approved
        # structured rule, so don't duplicate it into the Pending list.
        job = {**_JOB_RUNNING, "tenant_id": "tenant_1", "command": "rm -rf /tmp/x", "created_by": "user_1"}
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo"):
            jr.get.return_value = job
            apr.exists_pending.return_value = False
            apr.list_by_agent.return_value = [{"host_rule": {"bin": "rm", "args": ["-rf", "/tmp/x"]}, "command": "rm -rf /tmp/x", "status": "approved"}]
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

    def test_blocked_k8s_agent_does_not_raise_approval(self):
        # A k8s agent's block is a HARD block (allowlist / no-shell / escape / local-file),
        # not an approvable write - writes are gated at submission - so no pending is raised.
        k8s_agent = {**_AGENT, "type": "k8s"}
        job = {**_JOB_RUNNING, "tenant_id": "tenant_1", "command": "helm install rel", "created_by": "user_1"}
        with patch("handlers.agent_job_result._verify_agent_token", return_value=k8s_agent), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo"):
            jr.get.return_value = job
            handle_agent_job_result(JOB_ID, {**_VALID_BODY, "blocked": True, "status": "FAILED"}, "tok")
        apr.create.assert_not_called()

    def test_sandbox_unavailable_block_does_not_raise_approval(self):
        # Fail-closed (no kernel write protection) is not fixable by approving the command,
        # so a "sandbox_unavailable" block must NOT create a pending request - otherwise a
        # blocked read lands in Pending, implying approval would unblock it (it wouldn't).
        job = {**_JOB_RUNNING, "tenant_id": "tenant_1", "command": "uname -a", "created_by": "user_1"}
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo"):
            jr.get.return_value = job
            handle_agent_job_result(
                JOB_ID,
                {**_VALID_BODY, "blocked": True, "block_reason": "sandbox_unavailable", "status": "FAILED"},
                "tok")
        apr.create.assert_not_called()

    def test_approval_required_block_still_raises(self):
        # The default (empty or "approval_required") block reason is an approvable write - a
        # pending request IS raised so an operator can permit it.
        job = {**_JOB_RUNNING, "tenant_id": "tenant_1", "command": "rm -rf /tmp/x", "created_by": "user_1"}
        with patch("handlers.agent_job_result._verify_agent_token", return_value=_AGENT), \
             patch("handlers.agent_job_result.jobs_repo") as jr, \
             patch("handlers.agent_job_result.approvals_repo") as apr, \
             patch("handlers.agent_job_result.users_repo") as ur:
            jr.get.return_value = job
            apr.list_by_agent.return_value = []
            apr.exists_pending.return_value = False
            ur.get.return_value = None
            handle_agent_job_result(
                JOB_ID,
                {**_VALID_BODY, "blocked": True, "block_reason": "approval_required", "status": "FAILED"},
                "tok")
        apr.create.assert_called_once()
