"""Contract test: the CLI (cli/reach) reads specific fields out of these backend
response envelopes. The CLI is a separate package (mock-tested in isolation), so a
rename here wouldn't fail its tests - this guards the seam by asserting the exact
field names the CLI depends on are present in the real handler output.
"""
import json
from unittest.mock import patch

from handlers.list_jobs import handle_list_jobs
from handlers.jobs_fanout import handle_fanout_by_tag
from handlers.cli_fleets import handle_cli_list_fleets, handle_cli_fleet_fanout
from handlers.list_agents import handle_list_agents

TENANT = "t1"
USER = {"user_id": "u1", "tenant_id": TENANT}
_HOST = {"agent_id": "a1", "tenant_id": TENANT, "status": "ACTIVE", "type": "host",
         "mode": "wild", "fleet_id": None, "tags": ["env:prod"], "hostname": "h1"}
_MEMBER = {**_HOST, "agent_id": "m1", "fleet_id": "fleet_1"}
_JOB = {"job_id": "j1", "agent_id": "a1", "tenant_id": TENANT, "command": "ls",
        "status": "SUCCEEDED", "exit_code": 0, "run_id": "batch_x", "created_at": "2026-01-01T00:00:00Z",
        "duration_ms": 5, "created_by": "u1"}
_FLEET = {"fleet_id": "fleet_1", "tenant_id": TENANT, "name": "web", "mode": "wild",
          "status": "ACTIVE", "tags": []}


def _body(r):
    assert r["statusCode"] in (200, 201), r
    return json.loads(r["body"])


def test_list_jobs_fields_consumed_by_cli():
    with patch("handlers.list_jobs._verify_tenant_token", return_value=USER), \
         patch("handlers.list_jobs.jobs_repo") as jr, patch("handlers.list_jobs.agents_repo") as ar:
        jr.list_by_tenant.return_value = [_JOB]
        ar.get.return_value = _HOST
        body = _body(handle_list_jobs("tok", None, 20))
    job = body["jobs"][0]
    for field in ("job_id", "agent_id", "agent_hostname", "agent_fleet_id", "run_id",
                  "command", "status", "exit_code", "duration_ms", "created_at"):
        assert field in job, f"CLI relies on job.{field}"


def test_tag_fanout_fields_consumed_by_cli():
    with patch("handlers.jobs_fanout._verify_tenant_token", return_value=USER), \
         patch("handlers.jobs_fanout.agents_repo") as ar, \
         patch("handlers.jobs_fanout.approvals_repo"), patch("handlers.jobs_fanout.jobs_repo"):
        ar.list_by_tenant.return_value = [_HOST]
        body = _body(handle_fanout_by_tag({"tag": "env:prod", "command": "uptime"}, "tok"))
    for field in ("tag", "type", "run_id", "dispatched", "jobs", "skipped"):
        assert field in body, f"CLI relies on fanout.{field}"
    assert {"job_id", "agent_id", "hostname"} <= set(body["jobs"][0])


def test_fleet_fanout_fields_consumed_by_cli():
    with patch("handlers.cli_fleets._verify_tenant_token", return_value=USER), \
         patch("handlers.cli_fleets.fleets_repo") as fr, patch("handlers.cli_fleets.agents_repo") as ar, \
         patch("handlers.cli_fleets.jobs_repo"):
        fr.get.return_value = _FLEET
        ar.list_by_tenant.return_value = [{**_MEMBER, "status": "ACTIVE"}]
        body = _body(handle_cli_fleet_fanout("fleet_1", {"command": "uptime"}, "tok"))
    for field in ("fleet_id", "command", "run_id", "dispatched", "jobs", "skipped"):
        assert field in body, f"CLI relies on fleet fanout.{field}"


def test_list_fleets_fields_consumed_by_cli():
    with patch("handlers.cli_fleets._verify_tenant_token", return_value=USER), \
         patch("handlers.cli_fleets.fleets_repo") as fr, patch("handlers.cli_fleets.agents_repo"):
        fr.list_by_tenant.return_value = [_FLEET]
        fr.member_counts.return_value = {"fleet_1": 3}
        body = _body(handle_cli_list_fleets("tok"))
    f = body["fleets"][0]
    for field in ("fleet_id", "name", "mode", "status", "tags", "member_count", "writable"):
        assert field in f, f"CLI relies on fleet.{field}"


def test_list_agents_fields_consumed_by_cli():
    with patch("handlers.list_agents._verify_tenant_token", return_value=USER), \
         patch("handlers.list_agents.agents_repo") as ar:
        ar.list_by_tenant.return_value = [_HOST]
        body = _body(handle_list_agents("tok"))
    a = body["agents"][0]
    for field in ("agent_id", "status", "hostname", "type", "mode", "fleet_id", "writable", "tags"):
        assert field in a, f"CLI relies on agent.{field}"
