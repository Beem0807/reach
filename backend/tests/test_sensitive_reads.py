import json
from unittest.mock import patch

from handlers.create_job import handle_create_job
from handlers.get_job import handle_get_job
from handlers.list_jobs import handle_list_jobs
from shared.policy import (
    derive_k8s_rule,
    is_k8s_command_approved,
    is_k8s_secret_read,
    is_sensitive_read,
    normalize_k8s_rule,
)

TENANT = "tenant_1"
DEV = {"user_id": "dev_1", "tenant_id": TENANT, "role": "developer"}
OP = {"user_id": "op_1", "tenant_id": TENANT, "role": "operator"}
AGENT_ID = "agent_a"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class TestDetection:
    def test_host_sensitive_paths(self):
        for c in ["cat /home/u/.ssh/id_rsa", "cat .env", "cat app/.env.production",
                  "cat /etc/shadow", "cat /home/u/.aws/credentials", "cat /proc/self/environ",
                  "cat /etc/reach-agent/config.json", "tail server.pem"]:
            assert is_sensitive_read(c), c

    def test_host_non_sensitive(self):
        for c in ["ls -la", "cat README.md", "uname -a", "cat myfile.env", "git log", "docker ps"]:
            assert not is_sensitive_read(c), c

    def test_k8s_secret_reads(self):
        assert is_k8s_secret_read("kubectl get secret db -o yaml")
        assert is_k8s_secret_read("kubectl describe secret db")
        assert is_k8s_secret_read("kubectl get secrets -A")

    def test_k8s_non_secret_reads(self):
        assert not is_k8s_secret_read("kubectl get pods")
        assert not is_k8s_secret_read("kubectl delete secret x")  # a write, gated separately


# ---------------------------------------------------------------------------
# k8s approval chain - a Secret read is approvable
# ---------------------------------------------------------------------------

class TestK8sSecretApproval:
    def test_derive_rule_for_secret_read(self):
        assert derive_k8s_rule("kubectl get secret db -n prod") == {
            "verb": "get", "resource": "secrets", "namespace": "prod", "name": "db"}

    def test_normalize_accepts_get_secrets_only(self):
        assert normalize_k8s_rule({"verb": "get", "resource": "secrets"}) is not None
        assert normalize_k8s_rule({"verb": "get", "resource": "pods"}) is None  # read pods stays un-ruleable

    def test_gated_without_rule_allowed_with_rule(self):
        cmd = "kubectl get secret db"
        assert is_k8s_command_approved(cmd, []) is False
        rule = {"verb": "get", "resource": "secrets", "namespace": "*", "name": "*"}
        assert is_k8s_command_approved(cmd, [rule]) is True

    def test_ordinary_read_unaffected(self):
        assert is_k8s_command_approved("kubectl get pods", []) is True


# ---------------------------------------------------------------------------
# create_job gating - sensitive reads are gated like writes
# ---------------------------------------------------------------------------

class TestCreateJobGate:
    def _call(self, command, agent, user=DEV, approved=None):
        with patch("handlers.create_job._verify_tenant_token", return_value=user), \
             patch("handlers.create_job.agents_repo") as ar, \
             patch("handlers.create_job.approvals_repo") as apr, \
             patch("handlers.create_job.audit"), \
             patch("handlers.create_job.jobs_repo"):
            ar.get.return_value = agent
            apr.list_by_agent.return_value = approved or []
            return handle_create_job({"agent_id": AGENT_ID, "command": command}, "tok")

    def _agent(self, mode, type_="host"):
        return {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE", "mode": mode, "type": type_}

    def test_readonly_blocks_sensitive_read(self):
        r = self._call("cat /home/u/.ssh/id_rsa", self._agent("readonly"))
        assert r["statusCode"] == 403

    def test_wild_runs_sensitive_read(self):
        r = self._call("cat /home/u/.ssh/id_rsa", self._agent("wild"))
        assert r["statusCode"] == 201

    def test_approved_k8s_secret_read_rejected_and_raised(self):
        # no approved rule -> REJECTED job + pending approval (troubleshooting escape hatch)
        r = self._call("kubectl get secret db", self._agent("approved", "k8s"), approved=[])
        body = json.loads(r["body"])
        assert body.get("status") == "REJECTED"

    def test_approved_k8s_secret_read_runs_when_approved(self):
        rule = {"verb": "get", "resource": "secrets", "namespace": "*", "name": "*"}
        r = self._call("kubectl get secret db", self._agent("approved", "k8s"),
                       approved=[{"k8s_rule": rule, "status": "approved"}])
        assert r["statusCode"] == 201

    def test_normal_read_still_runs_in_readonly(self):
        r = self._call("cat README.md", self._agent("readonly"))
        assert r["statusCode"] == 201


# ---------------------------------------------------------------------------
# Developer-scoped job visibility
# ---------------------------------------------------------------------------

_JOB = {"job_id": "job_1", "agent_id": AGENT_ID, "tenant_id": TENANT, "created_by": "dev_1",
        "command": "cat x", "status": "SUCCEEDED", "exit_code": 0, "stdout": "o", "stderr": "",
        "duration_ms": 1, "expires_at": 9999999999}
_AGENT = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE", "mode": "wild"}


class TestDeveloperJobScope:
    def _get(self, job, user):
        with patch("handlers.get_job._verify_tenant_token", return_value=user), \
             patch("handlers.get_job.agents_repo") as ar, \
             patch("handlers.get_job.jobs_repo") as jr:
            ar.get.return_value = _AGENT
            jr.get.return_value = job
            return handle_get_job(job["job_id"], "tok")

    def test_developer_sees_own_job(self):
        assert self._get({**_JOB, "created_by": "dev_1"}, DEV)["statusCode"] == 200

    def test_developer_cannot_see_others_job(self):
        assert self._get({**_JOB, "created_by": "someone_else"}, DEV)["statusCode"] == 404

    def test_operator_sees_any_job(self):
        assert self._get({**_JOB, "created_by": "someone_else"}, OP)["statusCode"] == 200

    def test_list_filters_to_own_for_developer(self):
        rows = [{**_JOB, "job_id": "j1", "created_by": "dev_1"},
                {**_JOB, "job_id": "j2", "created_by": "other"}]
        with patch("handlers.list_jobs._verify_tenant_token", return_value=DEV), \
             patch("handlers.list_jobs.agents_repo") as ar, \
             patch("handlers.list_jobs.jobs_repo") as jr:
            ar.get.return_value = _AGENT
            jr.list_by_tenant.return_value = rows
            r = handle_list_jobs("tok", None, 50)
        ids = {j["job_id"] for j in json.loads(r["body"])["jobs"]}
        assert ids == {"j1"}


class TestSensitiveFlagOnJob:
    _A = {"agent_id": AGENT_ID, "tenant_id": TENANT, "status": "ACTIVE", "mode": "wild"}

    def _get(self, command):
        job = {**_JOB, "command": command, "created_by": "dev_1"}
        with patch("handlers.get_job._verify_tenant_token", return_value=DEV), \
             patch("handlers.get_job.agents_repo") as ar, \
             patch("handlers.get_job.jobs_repo") as jr:
            ar.get.return_value = self._A
            jr.get.return_value = job
            return json.loads(handle_get_job("job_1", "tok")["body"])

    def test_flag_true_for_sensitive(self):
        assert self._get("cat /home/u/.ssh/id_rsa")["sensitive"] is True
        assert self._get("kubectl get secret db")["sensitive"] is True

    def test_flag_false_for_normal(self):
        assert self._get("cat README.md")["sensitive"] is False
