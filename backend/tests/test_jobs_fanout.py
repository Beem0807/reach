"""Tests for handlers/jobs_fanout.py - tag fan-out (type-homogeneous, gated)."""
import json
from unittest.mock import patch

import pytest

from handlers.jobs_fanout import handle_fanout_by_tag, handle_list_tag_runs

TENANT = "tenant_1"
TOKEN = "tok_test"
USER = {"user_id": "u1", "tenant_id": TENANT}


@pytest.fixture(autouse=True)
def _runs_repo():
    with patch("handlers.jobs_fanout.runs_repo") as r:
        r.get.return_value = None
        r.list_by_tenant.return_value = []
        yield r

def _host(aid, **kw):
    return {"agent_id": aid, "tenant_id": TENANT, "status": "ACTIVE", "type": "host",
            "mode": "wild", "fleet_id": None, "tags": ["env:prod"], "hostname": aid, **kw}

def _k8s(aid, **kw):
    return {**_host(aid), "type": "k8s", **kw}


def _call(body, agents, user=USER):
    with patch("handlers.jobs_fanout._verify_tenant_token", return_value=user), \
         patch("handlers.jobs_fanout.agents_repo") as ar, \
         patch("handlers.jobs_fanout.approvals_repo") as apr, \
         patch("handlers.jobs_fanout.jobs_repo") as jr:
        ar.list_by_tenant.return_value = agents
        apr.list_by_agent.return_value = []
        jr.create.return_value = None
        r = handle_fanout_by_tag(body, TOKEN)
    return r


class TestTagFanout:
    def test_dispatches_to_matching_hosts(self):
        r = _call({"tag": "env:prod", "command": "uptime"}, [_host("a1"), _host("a2")])
        body = json.loads(r["body"])
        assert r["statusCode"] == 201
        assert body["dispatched"] == 2 and body["type"] == "host"
        assert body["run_id"] and all(j["agent_id"] in ("a1", "a2") for j in body["jobs"])

    def test_all_jobs_share_run_id(self):
        r = _call({"tag": "env:prod", "command": "uptime"}, [_host("a1"), _host("a2")])
        with patch("handlers.jobs_fanout._verify_tenant_token", return_value=USER):
            pass
        assert json.loads(r["body"])["run_id"].startswith("run_")

    def test_excludes_fleet_members(self):
        r = _call({"tag": "env:prod", "command": "uptime"},
                  [_host("a1"), _host("m1", fleet_id="fleet_1")])
        assert json.loads(r["body"])["dispatched"] == 1

    def test_dry_run_returns_preview_without_dispatch(self, _runs_repo):
        with patch("handlers.jobs_fanout._verify_tenant_token", return_value=USER), \
             patch("handlers.jobs_fanout.agents_repo") as ar, \
             patch("handlers.jobs_fanout.approvals_repo") as apr, \
             patch("handlers.jobs_fanout.audit") as aud, \
             patch("handlers.jobs_fanout.jobs_repo") as jr:
            ar.list_by_tenant.return_value = [_host("a1"), _host("a2")]
            apr.list_by_agent.return_value = []
            r = handle_fanout_by_tag({"tag": "env:prod", "command": "uptime", "dry_run": True}, TOKEN)
        body = json.loads(r["body"])
        assert body["dry_run"] is True and body["matched"] == 2 and body["type"] == "host"
        # "uptime" is a read -> the platform read default (auto / continue).
        assert body["wave_strategy"] == "auto" and body["failure_policy"] == "continue"
        jr.create.assert_not_called()          # nothing dispatched
        _runs_repo.create.assert_not_called()
        aud.write.assert_not_called()          # no audit for a preview

    def test_dispatch_writes_one_run_audit_event(self):
        with patch("handlers.jobs_fanout._verify_tenant_token", return_value=USER), \
             patch("handlers.jobs_fanout.agents_repo") as ar, \
             patch("handlers.jobs_fanout.approvals_repo") as apr, \
             patch("handlers.jobs_fanout.audit") as aud, \
             patch("handlers.jobs_fanout.jobs_repo") as jr, \
             patch("handlers.jobs_fanout.runs_repo") as rr:
            ar.list_by_tenant.return_value = [_host("a1"), _host("a2")]
            apr.list_by_agent.return_value = []
            jr.create.return_value = None
            rr.get.return_value = None
            handle_fanout_by_tag({"tag": "env:prod", "command": "uptime"}, TOKEN, ip="1.2.3.4")
        aud.write.assert_called_once()
        args, kwargs = aud.write.call_args
        assert args[0] == "run.dispatched"
        assert kwargs["ip_address"] == "1.2.3.4"
        assert kwargs["resource_type"] == "run"
        assert kwargs["metadata"]["scope"] == "tag" and kwargs["metadata"]["tag"] == "env:prod"
        assert kwargs["metadata"]["dispatched"] == 2

    def test_mixed_types_without_type_is_409(self):
        r = _call({"tag": "env:prod", "command": "uptime"}, [_host("a1"), _k8s("k1")])
        assert r["statusCode"] == 409
        assert "both host and k8s" in json.loads(r["body"])["error"]

    def test_type_host_selects_hosts_only(self):
        r = _call({"tag": "env:prod", "command": "uptime", "type": "host"},
                  [_host("a1"), _k8s("k1")])
        body = json.loads(r["body"])
        assert body["dispatched"] == 1 and body["jobs"][0]["agent_id"] == "a1"

    def test_type_k8s_selects_k8s(self):
        r = _call({"tag": "env:prod", "command": "kubectl get pods", "type": "k8s"},
                  [_host("a1"), _k8s("k1")])
        body = json.loads(r["body"])
        assert body["dispatched"] == 1 and body["type"] == "k8s"

    def test_k8s_unapproved_write_in_approved_mode_skipped(self):
        # A k8s write not covered by an approved rule must NOT dispatch (gated backend-side).
        k = _k8s("k1", mode="approved")
        r = _call({"tag": "env:prod", "command": "kubectl delete pods -n prod", "type": "k8s"}, [k])
        body = json.loads(r["body"])
        assert body["dispatched"] == 0
        assert body["skipped"][0]["reason"] == "not pre-approved (k8s rule)"

    def test_readonly_mode_skips_writes(self):
        r = _call({"tag": "env:prod", "command": "rm -rf /tmp/x"}, [_host("a1", mode="readonly")])
        body = json.loads(r["body"])
        assert body["dispatched"] == 0 and body["skipped"][0]["reason"] == "readonly mode"

    def test_reads_dispatch_in_readonly_mode(self):
        r = _call({"tag": "env:prod", "command": "uptime"}, [_host("a1", mode="readonly")])
        assert json.loads(r["body"])["dispatched"] == 1

    def test_no_matching_agents_404(self):
        r = _call({"tag": "nope", "command": "uptime"}, [_host("a1")])
        assert r["statusCode"] == 404

    def test_missing_command_400(self):
        r = _call({"tag": "env:prod"}, [_host("a1")])
        assert r["statusCode"] == 400

    def test_blocked_command_403(self):
        with patch("handlers.jobs_fanout._is_blocked", return_value=True):
            r = _call({"tag": "env:prod", "command": "rm -rf /"}, [_host("a1")])
        assert r["statusCode"] == 403

    def test_unauthorized(self):
        with patch("handlers.jobs_fanout._verify_tenant_token", return_value=None):
            assert handle_fanout_by_tag({"tag": "x", "command": "y"}, "bad")["statusCode"] == 401

    # --- Structured host writes (parity with POST /jobs and fleet fan-out) ---

    def _created_jobs(self, body, agents):
        """Run a fan-out and return the job payloads passed to jobs_repo.create."""
        with patch("handlers.jobs_fanout._verify_tenant_token", return_value=USER), \
             patch("handlers.jobs_fanout.agents_repo") as ar, \
             patch("handlers.jobs_fanout.approvals_repo") as apr, \
             patch("handlers.jobs_fanout.jobs_repo") as jr:
            ar.list_by_tenant.return_value = agents
            apr.list_by_agent.return_value = []
            r = handle_fanout_by_tag(body, TOKEN)
            created = [c.args[0] for c in jr.create.call_args_list]
        return r, created

    def test_host_write_carries_structured_argv(self):
        _, created = self._created_jobs(
            {"tag": "env:prod", "command": "systemctl restart nginx"}, [_host("a1")])
        assert len(created) == 1
        assert created[0]["argv"] == ["systemctl", "restart", "nginx"]
        assert created[0]["is_write"] is True

    def test_read_has_no_argv(self):
        _, created = self._created_jobs({"tag": "env:prod", "command": "uptime"}, [_host("a1")])
        assert created[0]["argv"] is None

    def test_shell_operator_write_runs_freeform_in_wild(self):
        # A write with a pipe can't be structured; in wild it still dispatches (argv None).
        _, created = self._created_jobs(
            {"tag": "env:prod", "command": "systemctl restart nginx | tee /tmp/x"}, [_host("a1")])
        assert len(created) == 1 and created[0]["argv"] is None

    def test_shell_operator_write_skipped_in_approved_mode(self):
        # Unstructurable (and so unapprovable) -> skipped for an approved-mode host.
        r = _call({"tag": "env:prod", "command": "systemctl restart nginx | tee /tmp/x"},
                  [_host("a1", mode="approved")])
        body = json.loads(r["body"])
        assert body["dispatched"] == 0
        assert body["skipped"][0]["reason"] == "shell operators can't be structured (approved mode)"

    def test_structured_write_dispatches_in_approved_mode(self):
        # A structurable write dispatches; the agent enforces its host rules (like fleets).
        _, created = self._created_jobs(
            {"tag": "env:prod", "command": "systemctl restart nginx"}, [_host("a1", mode="approved")])
        assert len(created) == 1 and created[0]["argv"] == ["systemctl", "restart", "nginx"]


# --- Tag runs (standalone fan-out batches) -----------------------------------
from handlers.jobs_fanout import handle_list_tag_runs


def _run_row(run_id, tag="env:prod", fleet_id=None, ok=1, failed=1, pending=0, members=2, state="partial"):
    return {"run_id": run_id, "tenant_id": TENANT, "fleet_id": fleet_id, "tag": tag,
            "command": "uptime", "created_at": "2026-06-20T10:00:00Z", "dispatched": members,
            "state": state, "counts": {"ok": ok, "failed": failed, "pending": pending, "running": 0}}


class TestTagRuns:
    def _call(self, run_rows, _runs_repo):
        _runs_repo.list_by_tenant.return_value = run_rows
        with patch("handlers.jobs_fanout._verify_tenant_token", return_value=USER):
            return handle_list_tag_runs(TOKEN)

    def test_lists_tag_runs_with_counts(self, _runs_repo):
        r = self._call([_run_row("b1")], _runs_repo)
        body = json.loads(r["body"])
        assert r["statusCode"] == 200
        run = body["runs"][0]
        assert run["run_id"] == "b1" and run["members"] == 2 and run["ok"] == 1 and run["failed"] == 1
        assert run["tag"] == "env:prod"

    def test_excludes_fleet_runs(self, _runs_repo):
        # The runs table holds fleet + tag runs; the tag-runs view keeps only tag runs.
        rows = [_run_row("b1", fleet_id=None), _run_row("b2", fleet_id="fleet_1", tag=None)]
        body = json.loads(self._call(rows, _runs_repo)["body"])
        assert [run["run_id"] for run in body["runs"]] == ["b1"]

    def test_running_counts_as_pending(self, _runs_repo):
        rows = [_run_row("b1", ok=0, failed=0, pending=0, members=1, state="running")]
        rows[0]["counts"] = {"ok": 0, "failed": 0, "pending": 0, "running": 1}
        run = json.loads(self._call(rows, _runs_repo)["body"])["runs"][0]
        assert run["pending"] == 1 and run["ok"] == 0

    def test_pagination_full_page_sets_next_cursor(self, _runs_repo):
        # A full raw page (len == limit) sets next_cursor from the last raw row, so Next
        # keeps paging even if some rows were fleet runs filtered out of this page.
        rows = [_run_row(f"b{i}") for i in range(3)]
        rows[-1]["created_at"] = "2026-06-19T00:00:00Z"
        _runs_repo.list_by_tenant.return_value = rows
        with patch("handlers.jobs_fanout._verify_tenant_token", return_value=USER):
            r = handle_list_tag_runs(TOKEN, limit=3, cursor="2026-06-21T00:00:00Z")
        body = json.loads(r["body"])
        _runs_repo.list_by_tenant.assert_called_once_with(TENANT, limit=3, cursor="2026-06-21T00:00:00Z")
        assert body["next_cursor"] == "2026-06-19T00:00:00Z"

    def test_pagination_partial_page_no_next_cursor(self, _runs_repo):
        r = self._call([_run_row("b1")], _runs_repo)   # 1 row, default limit 20
        assert json.loads(r["body"])["next_cursor"] is None

    def test_unauthorized(self):
        with patch("handlers.jobs_fanout._verify_tenant_token", return_value=None):
            assert handle_list_tag_runs("bad")["statusCode"] == 401
