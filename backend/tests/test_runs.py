import json
from unittest.mock import patch

from handlers.runs import handle_get_run

TENANT = "tenant_acme"
FLEET_ID = "fleet_1"
ADMIN = {"user_id": "u_admin", "tenant_id": TENANT, "role": "admin", "username": "admin"}
_FLEET = {"fleet_id": FLEET_ID, "tenant_id": TENANT, "name": "web-prod", "status": "ACTIVE"}
RUN = "batch_deadbeef"


def _run_row(fleet_id=FLEET_ID, tag=None):
    return {"run_id": RUN, "tenant_id": TENANT, "fleet_id": fleet_id, "tag": tag,
            "command": "uptime", "created_by": "u_admin", "created_at": "2026-06-01T10:00:00Z",
            "dispatched": 3, "skipped_count": 0, "capped_count": 0, "cap_applied": 25,
            "state": "running", "counts": {"ok": 0, "failed": 0, "pending": 3, "running": 0}}


def _fleet_jobs():
    return [
        {"agent_id": "a1", "run_id": RUN, "run_fleet_id": FLEET_ID, "command": "uptime",
         "status": "SUCCEEDED", "exit_code": 0, "created_at": "2026-06-01T10:00:00Z"},
        {"agent_id": "a2", "run_id": RUN, "run_fleet_id": FLEET_ID, "command": "uptime",
         "status": "FAILED", "exit_code": 1, "stderr": "boom", "created_at": "2026-06-01T10:00:01Z"},
        {"agent_id": "a3", "run_id": RUN, "run_fleet_id": FLEET_ID, "command": "uptime",
         "status": "PENDING", "exit_code": None, "created_at": "2026-06-01T10:00:02Z"},
    ]


def _call(jobs, user=ADMIN, fleet=_FLEET, run=None):
    with patch("handlers.runs._verify_tenant_token", return_value=user), \
         patch("handlers.runs.runs_repo") as rr, \
         patch("handlers.runs.jobs_repo") as jr, \
         patch("handlers.runs.fleets_repo") as fr, \
         patch("handlers.runs.agents_repo") as ar:
        rr.get.return_value = run if run is not None else _run_row()
        jr.list_by_run.return_value = jobs
        fr.get.return_value = fleet
        ar.get.side_effect = lambda aid: {"agent_id": aid, "tenant_id": TENANT}
        return handle_get_run("tok", RUN)


class TestGetRun:
    def test_unauthorized(self):
        with patch("handlers.runs._verify_tenant_token", return_value=None):
            assert handle_get_run("bad", RUN)["statusCode"] == 401

    def test_unknown_run_404(self):
        with patch("handlers.runs._verify_tenant_token", return_value=ADMIN), \
             patch("handlers.runs.runs_repo") as rr:
            rr.get.return_value = None
            r = handle_get_run("tok", RUN)
        assert r["statusCode"] == 404

    def test_aggregates_state_and_counts(self):
        body = json.loads(_call(_fleet_jobs())["body"])
        assert body["run_id"] == RUN and body["fleet_id"] == FLEET_ID
        assert body["counts"] == {"ok": 1, "failed": 1, "pending": 1, "running": 0}
        assert body["total"] == 3 and body["terminal"] is False and body["state"] == "running"
        assert body["command"] == "uptime"

    def test_terminal_partial_when_some_failed(self):
        jobs = [j for j in _fleet_jobs() if j["status"] != "PENDING"]  # 1 ok, 1 failed, all done
        body = json.loads(_call(jobs)["body"])
        assert body["terminal"] is True and body["state"] == "partial"

    def test_failures_drilldown_included(self):
        body = json.loads(_call(_fleet_jobs())["body"])
        assert [f["agent_id"] for f in body["failures"]] == ["a2"]
        assert body["failures"][0]["exit_code"] == 1 and body["failures"][0]["stderr"] == "boom"

    def test_fleet_access_denied_404(self):
        # Fleet belongs to a different tenant -> not accessible -> 404 (not a leak).
        other = {**_FLEET, "tenant_id": "tenant_other"}
        r = _call(_fleet_jobs(), fleet=other)
        assert r["statusCode"] == 404

    def test_tag_run_filters_to_accessible_agents(self):
        # A standalone (tag) run: no run_fleet_id, gated per-agent.
        jobs = [{"agent_id": "a1", "run_id": RUN, "run_tag": "env:prod", "command": "df -h",
                 "status": "SUCCEEDED", "exit_code": 0, "created_at": "2026-06-01T10:00:00Z"}]
        with patch("handlers.runs._verify_tenant_token", return_value=ADMIN), \
             patch("handlers.runs.runs_repo") as rr, \
             patch("handlers.runs.jobs_repo") as jr, \
             patch("handlers.runs.agents_repo") as ar:
            rr.get.return_value = _run_row(fleet_id=None, tag="env:prod")
            jr.list_by_run.return_value = jobs
            ar.get.return_value = {"agent_id": "a1", "tenant_id": TENANT}
            r = handle_get_run("tok", RUN)
        body = json.loads(r["body"])
        assert body["tag"] == "env:prod" and body["state"] == "succeeded"
