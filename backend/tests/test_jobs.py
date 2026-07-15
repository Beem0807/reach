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

    def test_dry_run_classifies_without_creating_or_auditing(self):
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.audit") as aud, \
             patch("handlers.create_job.jobs_repo") as jr:
            ar.get.return_value = _AGENT_ACTIVE
            r = handle_create_job({"agent_id": AGENT_ID, "command": "rm -rf /tmp/x", "dry_run": True}, "tok")
        body = json.loads(r["body"])
        assert body["dry_run"] is True and body["is_write"] is True
        assert body["agent_id"] == AGENT_ID and body["type"] == "host"  # heuristic classification
        jr.create.assert_not_called()
        aud.write.assert_not_called()

    def test_dry_run_read_is_not_write(self):
        r = self._call({"agent_id": AGENT_ID, "command": "uptime", "dry_run": True})
        body = json.loads(r["body"])
        assert body["dry_run"] is True and body["is_write"] is False

    def test_plain_command_auto_structured(self):
        # A plain command is transparently structured into an argv (no flag needed).
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.audit"), \
             patch("handlers.create_job.jobs_repo") as jr:
            ar.get.return_value = _AGENT_ACTIVE
            handle_create_job({"agent_id": AGENT_ID, "command": "systemctl restart nginx"}, "tok")
        assert jr.create.call_args[0][0]["argv"] == ["systemctl", "restart", "nginx"]

    def test_shell_command_stays_freeform(self):
        # A READ with shell features keeps the freeform (shell) path - argv is None.
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.audit"), \
             patch("handlers.create_job.jobs_repo") as jr:
            ar.get.return_value = _AGENT_ACTIVE
            handle_create_job({"agent_id": AGENT_ID, "command": "ps aux | grep nginx"}, "tok")
        assert jr.create.call_args[0][0]["argv"] is None

    def test_plain_read_stays_freeform(self):
        # Reads run as-is (freeform); only writes are structured.
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.audit"), \
             patch("handlers.create_job.jobs_repo") as jr:
            ar.get.return_value = _AGENT_ACTIVE
            handle_create_job({"agent_id": AGENT_ID, "command": "uptime"}, "tok")
        assert jr.create.call_args[0][0]["argv"] is None

    def test_piped_write_rejected_in_approved_mode(self):
        # In approved mode a shell/pipe write can't be a structured rule -> rejected.
        approved = {**_AGENT_ACTIVE, "mode": "approved"}
        r = self._call({"agent_id": AGENT_ID, "command": "cat x | tee /etc/passwd"}, agent=approved)
        assert r["statusCode"] == 400
        assert "shell operators" in json.loads(r["body"])["error"]

    def test_piped_write_allowed_freeform_in_wild_mode(self):
        # Wild mode has no approval/sandbox - a pipe write runs freeform (argv None).
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.audit"), \
             patch("handlers.create_job.jobs_repo") as jr:
            ar.get.return_value = _AGENT_ACTIVE   # mode wild
            r = handle_create_job({"agent_id": AGENT_ID, "command": "cat x | tee /var/log/y"}, "tok")
        assert r["statusCode"] == 201
        assert jr.create.call_args[0][0]["argv"] is None   # freeform

    def test_structured_argv_stores_argv_and_derives_command(self):
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.audit"), \
             patch("handlers.create_job.jobs_repo") as jr:
            ar.get.return_value = _AGENT_ACTIVE
            r = handle_create_job({"agent_id": AGENT_ID, "argv": ["systemctl", "restart", "nginx"]}, "tok")
        assert r["statusCode"] == 201
        created = jr.create.call_args[0][0]
        assert created["argv"] == ["systemctl", "restart", "nginx"]
        assert created["command"] == "systemctl restart nginx"   # display form

    def test_structured_argv_invalid_rejected(self):
        r = self._call({"agent_id": AGENT_ID, "argv": []})
        assert r["statusCode"] == 400

    def test_structured_argv_rejected_for_k8s_agent(self):
        k8s = {**_AGENT_ACTIVE, "type": "k8s"}
        r = self._call({"agent_id": AGENT_ID, "argv": ["kubectl", "get", "pods"]}, agent=k8s)
        assert r["statusCode"] == 400
        assert "host agents" in json.loads(r["body"])["error"]

    def test_dry_run_structured_returns_argv(self):
        r = self._call({"agent_id": AGENT_ID, "argv": ["systemctl", "restart", "nginx"], "dry_run": True})
        body = json.loads(r["body"])
        assert body["structured"] is True and body["argv"] == ["systemctl", "restart", "nginx"]
        assert body["is_write"] is True   # heuristic on the joined display command

    def test_dispatch_writes_job_audit_event(self):
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.audit") as aud, \
             patch("handlers.create_job.jobs_repo"):
            ar.get.return_value = _AGENT_ACTIVE
            handle_create_job({"agent_id": AGENT_ID, "command": "ls"}, "tok", ip="5.6.7.8")
        aud.write.assert_called_once()
        args, kwargs = aud.write.call_args
        assert args[0] == "job.dispatched"
        assert kwargs["ip_address"] == "5.6.7.8"
        assert kwargs["resource_type"] == "job"
        assert kwargs["metadata"]["agent_id"] == AGENT_ID and kwargs["metadata"]["is_write"] is False

    def test_rejected_write_writes_no_job_audit(self):
        # An unapproved k8s write is REJECTED (queued for approval), not dispatched -> no job.dispatched.
        k8s = {**_AGENT_ACTIVE, "type": "k8s", "mode": "approved"}
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.approvals_repo") as apr, \
             patch("handlers.create_job.audit") as aud, \
             patch("handlers.create_job.jobs_repo"):
            ar.get.return_value = k8s
            apr.list_by_agent.return_value = []
            r = handle_create_job({"agent_id": AGENT_ID, "command": "kubectl delete pods -n prod"}, "tok")
        assert json.loads(r["body"])["status"] == "REJECTED"
        for c in aud.write.call_args_list:
            assert c.args[0] != "job.dispatched"

    def test_no_access_returns_404(self):
        restricted_user = {**USER, "readwrite_agent_ids": ["agent_other"]}
        r = self._call({"agent_id": AGENT_ID, "command": "ls"}, user=restricted_user)
        assert r["statusCode"] == 404

    # Per-user read-only capability: a read-only grant blocks writes in ANY mode,
    # but read commands still run.
    def test_readonly_user_blocked_from_write_even_in_wild(self):
        ro_user = {**USER, "readonly_agent_ids": [AGENT_ID]}
        r = self._call({"agent_id": AGENT_ID, "command": "rm file.txt"}, agent=_AGENT_ACTIVE, user=ro_user)
        assert r["statusCode"] == 403
        assert "read-only" in json.loads(r["body"])["error"]

    def test_readonly_user_can_still_read(self):
        ro_user = {**USER, "readonly_agent_ids": [AGENT_ID]}
        r = self._call({"agent_id": AGENT_ID, "command": "docker ps"}, agent=_AGENT_ACTIVE, user=ro_user)
        assert r["statusCode"] == 201

    def test_readwrite_user_can_write(self):
        rw_user = {**USER, "readwrite_agent_ids": [AGENT_ID]}
        r = self._call({"agent_id": AGENT_ID, "command": "rm file.txt"}, agent=_AGENT_ACTIVE, user=rw_user)
        assert r["statusCode"] == 201


_AGENT_K8S_READONLY = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE", "mode": "readonly", "type": "k8s"}
_AGENT_K8S_APPROVED = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE", "mode": "approved", "type": "k8s"}


class TestCreateJobK8s:
    """k8s agents are gated at submission (verb-aware, default-deny)."""

    def _call(self, command, agent, approved=None):
        # `approved` is a list of structured k8s rules ({verb, resource, namespace, name}).
        with patch("handlers.create_job._verify_tenant_token", return_value=USER), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.jobs_repo") as jr, \
             patch("handlers.create_job.approvals_repo") as apr, \
             patch("handlers.create_job.users_repo") as ur:
            ar.get.return_value = agent
            apr.list_by_agent.return_value = [{"command": "", "k8s_rule": rule} for rule in (approved or [])]
            apr.exists_pending.return_value = False
            ur.get.return_value = {"name": "Alice"}
            r = handle_create_job({"agent_id": AGENT_ID, "command": command}, "tok")
            return r, jr, apr

    def test_readonly_allows_kubectl_read(self):
        r, _, _ = self._call("kubectl get pods", _AGENT_K8S_READONLY)
        assert r["statusCode"] == 201

    def test_readonly_blocks_write_the_old_regex_missed(self):
        # `cordon` is not in the legacy regex; the verb classifier catches it.
        r, _, _ = self._call("kubectl cordon node-1", _AGENT_K8S_READONLY)
        assert r["statusCode"] == 403

    def test_readonly_blocks_pipeline_with_write_stage(self):
        r, _, _ = self._call("kubectl get x -o yaml | kubectl apply -f -", _AGENT_K8S_READONLY)
        assert r["statusCode"] == 403

    def test_approved_read_dispatches(self):
        r, jr, _ = self._call("kubectl get pods | grep x", _AGENT_K8S_APPROVED)
        assert json.loads(r["body"])["status"] == "PENDING"

    def test_approved_unapproved_write_is_rejected_and_raises_approval(self):
        r, jr, apr = self._call("kubectl delete pod x", _AGENT_K8S_APPROVED)
        body = json.loads(r["body"])
        assert body["status"] == "REJECTED"
        assert body["approval_required"] is True
        apr.create.assert_called_once()
        # The recorded job is REJECTED, never dispatched.
        assert jr.create.call_args[0][0]["status"] == "REJECTED"

    def test_approved_preapproved_write_dispatches(self):
        # A rule permitting delete pods in the default namespace (any name) covers this.
        rule = {"verb": "delete", "resource": "pods", "namespace": "default", "name": "*"}
        r, jr, apr = self._call("kubectl delete pod x", _AGENT_K8S_APPROVED, approved=[rule])
        assert json.loads(r["body"])["status"] == "PENDING"
        apr.create.assert_not_called()

    def test_approved_rule_for_other_namespace_does_not_match(self):
        rule = {"verb": "delete", "resource": "pods", "namespace": "team-a", "name": "*"}
        r, jr, apr = self._call("kubectl delete pod x -n team-b", _AGENT_K8S_APPROVED, approved=[rule])
        assert json.loads(r["body"])["status"] == "REJECTED"

    def test_derived_rule_stored_on_pending_approval(self):
        r, jr, apr = self._call("kubectl delete pod nginx -n team-a", _AGENT_K8S_APPROVED)
        assert json.loads(r["body"])["status"] == "REJECTED"
        created = apr.create.call_args[0][0]
        assert created["k8s_rule"] == {"verb": "delete", "resource": "pods", "namespace": "team-a", "name": "nginx"}

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
        restricted_user = {**USER, "readwrite_agent_ids": ["agent_other"]}
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

    def test_fleet_member_job_is_fetchable(self):
        # A fleet member's job is fetchable by id like any other (fleet-aware access).
        member = {**_AGENT_ACTIVE, "fleet_id": "fleet_1"}
        fleet_user = {**USER, "readwrite_fleet_ids": ["fleet_1"], "readonly_fleet_ids": []}
        r = self._call(user=fleet_user, agent=member)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["job_id"] == "job_1"

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

    def test_batch_expansion_survives_reaped_members(self):
        # A fleet fan-out batch expands via run_id even after its members are reaped:
        # the jobs carry run_fleet_id, so access is gated by the fleet (not per-agent,
        # which would drop jobs whose agent record is gone).
        reaped = [
            {**_JOB, "job_id": "j1", "agent_id": "gone_1", "run_id": "batch_r", "run_fleet_id": "fleet_1"},
            {**_JOB, "job_id": "j2", "agent_id": "gone_2", "run_id": "batch_r", "run_fleet_id": "fleet_1"},
        ]
        with patch("handlers.list_jobs._verify_tenant_token", return_value=USER), \
             patch("handlers.list_jobs.jobs_repo") as jr, \
             patch("handlers.list_jobs.fleets_repo") as flr, \
             patch("handlers.list_jobs.agents_repo") as agr:
            jr.list_by_run.return_value = reaped
            flr.get.return_value = {"fleet_id": "fleet_1", "tenant_id": TENANT}
            agr.get.return_value = None   # members reaped - no agent records
            r = handle_list_jobs("tok", None, 20, run_id="batch_r")
        body = json.loads(r["body"])
        assert r["statusCode"] == 200
        assert {j["job_id"] for j in body["jobs"]} == {"j1", "j2"}
        # agent_fleet_id falls back to the stamped fleet id when the agent is gone.
        assert all(j["agent_fleet_id"] == "fleet_1" for j in body["jobs"])

    def test_batch_expansion_denied_for_inaccessible_fleet(self):
        restricted_user = {**USER, "readwrite_fleet_ids": ["fleet_other"], "readonly_fleet_ids": [],
                           "readwrite_agent_ids": [], "readonly_agent_ids": []}
        job = {**_JOB, "run_id": "batch_r", "run_fleet_id": "fleet_1"}
        with patch("handlers.list_jobs._verify_tenant_token", return_value=restricted_user), \
             patch("handlers.list_jobs.jobs_repo") as jr, \
             patch("handlers.list_jobs.fleets_repo") as flr, \
             patch("handlers.list_jobs.agents_repo"):
            jr.list_by_run.return_value = [job]
            flr.get.return_value = {"fleet_id": "fleet_1", "tenant_id": TENANT}
            r = handle_list_jobs("tok", None, 20, run_id="batch_r")
        assert r["statusCode"] == 404

    def test_agent_filter_inaccessible_returns_404(self):
        restricted_user = {**USER, "readwrite_agent_ids": ["agent_other"]}
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
        restricted_user = {**USER, "readwrite_agent_ids": [AGENT_ID]}
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


# ---------------------------------------------------------------------------
# handle_list_jobs - fleet scope
# ---------------------------------------------------------------------------

_FLEET = {"fleet_id": "fleet_1", "tenant_id": TENANT, "name": "web-asg", "status": "ACTIVE"}
_MEMBER = {"agent_id": "agent_m1", "tenant_id": TENANT, "fleet_id": "fleet_1", "status": "ACTIVE"}
_MJOB = {**_JOB, "job_id": "job_m", "agent_id": "agent_m1"}
_OTHER_JOB = {**_JOB, "job_id": "job_o", "agent_id": "agent_other"}


class TestListFleetJobs:
    def _call(self, jobs, fleet=_FLEET, members=None, user=USER):
        members = members if members is not None else [_MEMBER]
        with patch("handlers.list_jobs._verify_tenant_token", return_value=user), \
             patch("handlers.list_jobs.jobs_repo") as jr, \
             patch("handlers.list_jobs.agents_repo") as agr, \
             patch("handlers.list_jobs.fleets_repo") as fr:
            jr.list_by_tenant.return_value = jobs
            agr.list_by_fleet.return_value = members
            agr.get.side_effect = lambda aid: next((m for m in members if m["agent_id"] == aid), None)
            fr.get.return_value = fleet
            return handle_list_jobs("tok", None, 20, fleet_id="fleet_1")

    def test_filters_to_fleet_members(self):
        r = self._call([_MJOB, _OTHER_JOB])
        jobs = json.loads(r["body"])["jobs"]
        assert [j["job_id"] for j in jobs] == ["job_m"]

    def test_unknown_fleet_404(self):
        r = self._call([_MJOB], fleet=None)
        assert r["statusCode"] == 404

    def test_no_access_fleet_404(self):
        restricted = {**USER, "readwrite_fleet_ids": ["other"], "readonly_fleet_ids": []}
        r = self._call([_MJOB], user=restricted)
        assert r["statusCode"] == 404


class TestListJobsByBatch:
    def test_filters_to_batch(self):
        # The run detail reads the exact member jobs via the indexed run_id.
        run_jobs = [
            {**_JOB, "job_id": "j1", "agent_id": "agent_a", "run_id": "batch_a"},
            {**_JOB, "job_id": "j2", "agent_id": "agent_b", "run_id": "batch_a"},
        ]
        with patch("handlers.list_jobs._verify_tenant_token", return_value=USER), \
             patch("handlers.list_jobs.jobs_repo") as jr, \
             patch("handlers.list_jobs.agents_repo") as agr:
            jr.list_by_run.return_value = run_jobs
            agr.get.return_value = _AGENT_ACTIVE
            r = handle_list_jobs("tok", None, 20, run_id="batch_a")
        ids = [j["job_id"] for j in json.loads(r["body"])["jobs"]]
        assert ids == ["j1", "j2"]
        jr.list_by_run.assert_called_once_with(TENANT, "batch_a")

    def test_jobs_carry_run_id(self):
        with patch("handlers.list_jobs._verify_tenant_token", return_value=USER), \
             patch("handlers.list_jobs.jobs_repo") as jr, \
             patch("handlers.list_jobs.agents_repo") as agr:
            jr.list_by_tenant.return_value = [{**_JOB, "run_id": "batch_z"}]
            agr.get.return_value = _AGENT_ACTIVE
            r = handle_list_jobs("tok", None, 20)
        assert json.loads(r["body"])["jobs"][0]["run_id"] == "batch_z"
