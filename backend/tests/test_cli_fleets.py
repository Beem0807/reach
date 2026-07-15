"""Tests for handlers/cli_fleets.py (API-token fleet surface: list, members, fan-out)."""
import json
from unittest.mock import patch

import pytest

from handlers.cli_fleets import (
    handle_cli_list_fleets,
    handle_cli_list_fleet_agents,
    handle_cli_list_fleet_approved,
    handle_cli_list_fleet_runs,
    handle_cli_fleet_fanout,
)


@pytest.fixture(autouse=True)
def _runs_repo():
    """The fan-out + run-list handlers now touch the runs table. Default: no prior run
    (get -> None), empty lists; a test overrides these when it needs a run to exist."""
    with patch("handlers.cli_fleets.runs_repo") as r:
        r.get.return_value = None
        r.list_by_fleet.return_value = []
        r.list_by_tenant.return_value = []
        yield r

TENANT_ID = "tenant_1"
FLEET_ID = "fleet_abc"
TOKEN = "tok_test"

_ADMIN = {"user_id": "u_admin", "tenant_id": TENANT_ID, "role": "admin"}
# A developer scoped read-only to the fleet.
_RO = {"user_id": "u_ro", "tenant_id": TENANT_ID, "role": "developer",
       "readwrite_fleet_ids": [], "readonly_fleet_ids": [FLEET_ID]}
# A developer with no fleet access.
_NONE = {"user_id": "u_none", "tenant_id": TENANT_ID, "role": "developer",
         "readwrite_fleet_ids": [], "readonly_fleet_ids": []}

_FLEET = {"fleet_id": FLEET_ID, "tenant_id": TENANT_ID, "name": "web-asg",
          "mode": "wild", "status": "ACTIVE", "tags": ["env:prod"]}
_M1 = {"agent_id": "agent_m1", "tenant_id": TENANT_ID, "fleet_id": FLEET_ID, "status": "ACTIVE",
       "mode": "wild", "hostname": "web-01", "type": "host"}
_M2 = {"agent_id": "agent_m2", "tenant_id": TENANT_ID, "fleet_id": FLEET_ID, "status": "INACTIVE",
       "mode": "wild", "hostname": "web-02", "type": "host"}
_STANDALONE = {"agent_id": "agent_s", "tenant_id": TENANT_ID, "fleet_id": None, "status": "ACTIVE"}


def _patch(user=_ADMIN, fleet=_FLEET, agents=None):
    agents = agents if agents is not None else [_M1, _M2, _STANDALONE]
    p_auth = patch("handlers.cli_fleets._verify_tenant_token", return_value=user)
    p_fr = patch("handlers.cli_fleets.fleets_repo")
    p_ar = patch("handlers.cli_fleets.agents_repo")
    p_jr = patch("handlers.cli_fleets.jobs_repo")
    return p_auth, p_fr, p_ar, p_jr, agents, fleet


class TestListFleets:
    def test_unauthorized(self):
        with patch("handlers.cli_fleets._verify_tenant_token", return_value=None):
            assert handle_cli_list_fleets("bad")["statusCode"] == 401

    def test_admin_sees_fleet_with_count_and_writable(self):
        pa, pf, par, pj, agents, fleet = _patch()
        with pa, pf as fr, par, pj:
            fr.list_by_tenant.return_value = [fleet]
            fr.member_counts.return_value = {FLEET_ID: 2}
            r = handle_cli_list_fleets(TOKEN)
        body = json.loads(r["body"])
        assert r["statusCode"] == 200
        assert len(body["fleets"]) == 1
        f = body["fleets"][0]
        assert f["fleet_id"] == FLEET_ID and f["member_count"] == 2 and f["writable"] is True

    def test_readonly_user_sees_fleet_not_writable(self):
        pa, pf, par, pj, agents, fleet = _patch(user=_RO)
        with pa, pf as fr, par, pj:
            fr.list_by_tenant.return_value = [fleet]
            fr.member_counts.return_value = {FLEET_ID: 2}
            r = handle_cli_list_fleets(TOKEN)
        assert json.loads(r["body"])["fleets"][0]["writable"] is False

    def test_no_access_user_sees_nothing(self):
        pa, pf, par, pj, agents, fleet = _patch(user=_NONE)
        with pa, pf as fr, par, pj:
            fr.list_by_tenant.return_value = [fleet]
            fr.member_counts.return_value = {FLEET_ID: 2}
            r = handle_cli_list_fleets(TOKEN)
        assert json.loads(r["body"])["fleets"] == []


class TestListFleetAgents:
    def test_lists_only_members(self):
        pa, pf, par, pj, agents, fleet = _patch()
        with pa, pf as fr, par as ar, pj:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = agents
            r = handle_cli_list_fleet_agents(FLEET_ID, TOKEN)
        body = json.loads(r["body"])
        assert {a["agent_id"] for a in body["agents"]} == {"agent_m1", "agent_m2"}
        assert body["fleet_name"] == "web-asg"

    def test_queries_by_fleet_index_not_tenant_scan(self):
        # Members are fetched via the fleet_id index, never by scanning all of a
        # tenant's agents - the scale fix for large fleets.
        pa, pf, par, pj, agents, fleet = _patch()
        with pa, pf as fr, par as ar, pj:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = [_M1, _M2]
            handle_cli_list_fleet_agents(FLEET_ID, TOKEN)
        ar.list_by_fleet.assert_called_once_with(FLEET_ID)
        ar.list_by_tenant.assert_not_called()

    def test_unknown_fleet_404(self):
        pa, pf, par, pj, agents, fleet = _patch()
        with pa, pf as fr, par, pj:
            fr.get.return_value = None
            r = handle_cli_list_fleet_agents(FLEET_ID, TOKEN)
        assert r["statusCode"] == 404

    def test_no_access_404(self):
        pa, pf, par, pj, agents, fleet = _patch(user=_NONE)
        with pa, pf as fr, par, pj:
            fr.get.return_value = fleet
            r = handle_cli_list_fleet_agents(FLEET_ID, TOKEN)
        assert r["statusCode"] == 404

    def _roster(self, n):
        return [{"agent_id": f"agent_{i:03d}", "tenant_id": TENANT_ID, "fleet_id": FLEET_ID,
                 "status": "ACTIVE", "mode": "wild", "hostname": f"web-{i:03d}", "type": "host"}
                for i in range(n)]

    def test_no_limit_returns_all_without_page_meta(self):
        pa, pf, par, pj, _, fleet = _patch()
        with pa, pf as fr, par as ar, pj:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = self._roster(30)
            r = handle_cli_list_fleet_agents(FLEET_ID, TOKEN)
        body = json.loads(r["body"])
        assert len(body["agents"]) == 30 and "total" not in body

    def test_pagination_returns_page_and_total(self):
        pa, pf, par, pj, _, fleet = _patch()
        with pa, pf as fr, par as ar, pj:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = self._roster(30)
            r = handle_cli_list_fleet_agents(FLEET_ID, TOKEN, limit=20, offset=20)
        body = json.loads(r["body"])
        assert body["total"] == 30 and body["limit"] == 20 and body["offset"] == 20
        assert len(body["agents"]) == 10
        assert body["agents"][0]["hostname"] == "web-020"  # deterministic order

    def test_q_filters_members_by_hostname(self):
        pa, pf, par, pj, _, fleet = _patch()
        with pa, pf as fr, par as ar, pj:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = [_M1, _M2]  # web-01, web-02
            r = handle_cli_list_fleet_agents(FLEET_ID, TOKEN, q="web-01", limit=20)
        body = json.loads(r["body"])
        assert body["total"] == 1 and body["agents"][0]["agent_id"] == "agent_m1"


class TestListFleetApproved:
    _APPROVED = {"approval_id": "appr_1", "fleet_id": FLEET_ID, "agent_id": None,
                 "command": "docker restart web", "status": "approved", "requested_by": "u_admin"}
    _PENDING = {**_APPROVED, "approval_id": "appr_2", "status": "pending"}

    def _call(self, status="approved", items=None, user=_ADMIN, fleet=_FLEET):
        pa, pf, par, pj, _agents, _fleet = _patch(user=user, fleet=fleet)
        with pa, pf as fr, par, pj, \
             patch("handlers.cli_fleets.approvals_repo") as apr:
            fr.get.return_value = fleet
            apr.list_by_fleet.return_value = items if items is not None else [self._APPROVED]
            r = handle_cli_list_fleet_approved(FLEET_ID, TOKEN, status=status)
        return r, apr

    def test_approved_returns_effective_commands(self):
        r, apr = self._call(status="approved", items=[self._APPROVED])
        body = json.loads(r["body"])
        assert r["statusCode"] == 200
        assert body["approved_commands"] == ["docker restart web"]
        apr.list_by_fleet.assert_called_once_with(FLEET_ID, status="approved")

    def test_pending_filters_to_caller(self):
        r, apr = self._call(status="pending", items=[self._PENDING])
        assert r["statusCode"] == 200
        apr.list_by_fleet.assert_called_once_with(FLEET_ID, status="pending", requested_by="u_admin")
        assert json.loads(r["body"])["approved_commands"] == []

    def test_invalid_status_400(self):
        r, _ = self._call(status="bogus")
        assert r["statusCode"] == 400

    def test_no_access_404(self):
        r, _ = self._call(user=_NONE)
        assert r["statusCode"] == 404

    def test_unauthorized(self):
        with patch("handlers.cli_fleets._verify_tenant_token", return_value=None):
            assert handle_cli_list_fleet_approved(FLEET_ID, "bad")["statusCode"] == 401


class TestFleetFanout:
    def test_dispatches_to_active_members_only(self):
        pa, pf, par, pj, agents, fleet = _patch()
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = agents
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)
        body = json.loads(r["body"])
        assert r["statusCode"] == 201
        assert body["dispatched"] == 1                      # only ACTIVE _M1
        assert body["jobs"][0]["agent_id"] == "agent_m1"
        assert {s["agent_id"] for s in body["skipped"]} == {"agent_m2"}  # INACTIVE skipped
        assert jr.create.call_count == 1

    def test_write_is_structured_into_argv(self, _runs_repo):
        fleet = {**_FLEET, "mode": "wild"}
        pa, pf, par, pj, agents, _ = _patch(agents=[_M1], fleet=fleet)
        with pa, pf as fr, par as ar, pj as jr, patch("handlers.cli_fleets.tenants_repo") as tr:
            fr.get.return_value = fleet
            tr.get.return_value = {"tenant_id": TENANT_ID, "settings": {}}
            ar.list_by_fleet.return_value = [_M1]
            jr.create.return_value = None
            handle_cli_fleet_fanout(FLEET_ID, {"command": "systemctl restart nginx"}, TOKEN)
        assert jr.create.call_args[0][0]["argv"] == ["systemctl", "restart", "nginx"]

    def test_wild_fleet_shell_write_runs_freeform(self, _runs_repo):
        fleet = {**_FLEET, "mode": "wild"}
        pa, pf, par, pj, agents, _ = _patch(agents=[_M1], fleet=fleet)
        with pa, pf as fr, par as ar, pj as jr, patch("handlers.cli_fleets.tenants_repo") as tr:
            fr.get.return_value = fleet
            tr.get.return_value = {"tenant_id": TENANT_ID, "settings": {}}
            ar.list_by_fleet.return_value = [_M1]
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "cat x | tee /var/log/y"}, TOKEN)
        assert r["statusCode"] == 201
        assert jr.create.call_args[0][0]["argv"] is None   # freeform in a wild fleet

    def test_approved_fleet_shell_write_rejected(self):
        fleet = {**_FLEET, "mode": "approved"}
        pa, pf, par, pj, agents, _ = _patch(agents=[_M1], fleet=fleet)
        with pa, pf as fr, par, pj:
            fr.get.return_value = fleet
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "cat x | tee /etc/passwd"}, TOKEN)
        assert r["statusCode"] == 400
        assert "shell operators" in json.loads(r["body"])["error"]

    def test_run_row_records_skip_detail(self, _runs_repo):
        # The run persists who/why was skipped, so it's clear later why a host didn't run.
        pa, pf, par, pj, agents, fleet = _patch()
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = agents   # _M1 ACTIVE, _M2 INACTIVE
            jr.create.return_value = None
            handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)
        run = _runs_repo.create.call_args[0][0]
        assert run["skipped_count"] == 1
        assert run["skipped"][0]["agent_id"] == "agent_m2"
        assert run["skipped"][0]["reason"].startswith("not active")

    def test_dispatch_writes_one_run_audit_event(self, _runs_repo):
        pa, pf, par, pj, agents, fleet = _patch(agents=[_M1])
        with pa, pf as fr, par as ar, pj as jr, \
             patch("handlers.cli_fleets.tenants_repo") as tr, \
             patch("handlers.cli_fleets.audit") as aud:
            fr.get.return_value = fleet
            tr.get.return_value = {"tenant_id": TENANT_ID, "settings": {}}
            ar.list_by_fleet.return_value = [_M1]
            jr.create.return_value = None
            handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN, ip="9.9.9.9")
        aud.write.assert_called_once()
        args, kwargs = aud.write.call_args
        assert args[0] == "run.dispatched"
        assert kwargs["ip_address"] == "9.9.9.9"
        assert kwargs["resource_type"] == "run"
        assert kwargs["metadata"]["scope"] == "fleet" and kwargs["metadata"]["fleet_id"] == FLEET_ID
        assert kwargs["metadata"]["dispatched"] == 1

    def test_dry_run_writes_no_audit_event(self, _runs_repo):
        pa, pf, par, pj, agents, fleet = _patch(agents=[_M1])
        with pa, pf as fr, par as ar, pj as jr, \
             patch("handlers.cli_fleets.tenants_repo") as tr, \
             patch("handlers.cli_fleets.audit") as aud:
            fr.get.return_value = fleet
            tr.get.return_value = {"tenant_id": TENANT_ID, "settings": {}}
            ar.list_by_fleet.return_value = [_M1]
            handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime", "dry_run": True}, TOKEN)
        aud.write.assert_not_called()
        _runs_repo.create.assert_not_called()

    def test_all_jobs_share_one_run_id(self):
        two_active = [_M1, {**_M2, "status": "ACTIVE"}]
        pa, pf, par, pj, agents, fleet = _patch(agents=two_active)
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = two_active
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)
        body = json.loads(r["body"])
        assert body["dispatched"] == 2
        run_ids = {c.args[0]["run_id"] for c in jr.create.mock_calls if c.args}
        assert len(run_ids) == 1 and body["run_id"] in run_ids

    def test_fanout_stamps_fleet_id_on_jobs(self):
        # Every fan-out job carries run_fleet_id so runs group durably by fleet,
        # not by joining back to (ephemeral) member records.
        pa, pf, par, pj, agents, fleet = _patch(agents=[_M1])
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = [_M1]
            handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)
        stamped = {c.args[0].get("run_fleet_id") for c in jr.create.mock_calls if c.args}
        assert stamped == {FLEET_ID}

    def _active_roster(self, n):
        return [{"agent_id": f"agent_{i:03d}", "tenant_id": TENANT_ID, "fleet_id": FLEET_ID,
                 "status": "ACTIVE", "mode": "wild", "hostname": f"web-{i:03d}"} for i in range(n)]

    def test_wave_policy_stages_in_batches_of_cap(self, _runs_repo):
        # A fleet write policy (auto/stop) with max_fanout=2 stages 5 members into waves
        # of 2 -> [2, 2, 1]; wave 0 runs now, the rest are HELD (nothing dropped).
        roster = self._active_roster(5)
        fleet = {**_FLEET, "max_fanout": 2,
                 "wave_policy": {"write": {"mode": "auto", "on_failure": "stop"}}}
        pa, pf, par, pj, agents, _ = _patch(agents=roster, fleet=fleet)
        with pa, pf as fr, par as ar, pj as jr, \
             patch("handlers.cli_fleets.tenants_repo") as tr:
            fr.get.return_value = fleet
            tr.get.return_value = {"tenant_id": TENANT_ID, "settings": {}}
            ar.list_by_fleet.return_value = roster
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "rm -rf /tmp/x"}, TOKEN)
        body = json.loads(r["body"])
        assert r["statusCode"] == 201
        assert body["dispatched"] == 2 and body["total"] == 5 and body["wave_total"] == 3
        statuses = sorted(c.args[0]["status"] for c in jr.create.mock_calls if c.args)
        assert statuses == ["HELD", "HELD", "HELD", "PENDING", "PENDING"]
        assert ar.set_active_until.call_count == 2   # only wave-0 agents reactivated
        run = _runs_repo.create.call_args[0][0]
        assert run["wave_total"] == 3 and run["rollout"]["waves"] == [2, 2, 1]
        assert run["rollout"]["mode"] == "auto" and run["rollout"]["on_failure"] == "stop"

    def test_concurrency_lowers_wave_size_below_cap(self, _runs_repo):
        # cap (max_fanout) 4, policy concurrency 2 -> waves of 2 over 5 members = [2,2,1].
        roster = self._active_roster(5)
        fleet = {**_FLEET, "max_fanout": 4,
                 "wave_policy": {"write": {"mode": "auto", "on_failure": "stop", "concurrency": 2}}}
        pa, pf, par, pj, agents, _ = _patch(agents=roster, fleet=fleet)
        with pa, pf as fr, par as ar, pj as jr, \
             patch("handlers.cli_fleets.tenants_repo") as tr:
            fr.get.return_value = fleet
            tr.get.return_value = {"tenant_id": TENANT_ID, "settings": {}}
            ar.list_by_fleet.return_value = roster
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "rm -rf /tmp/x"}, TOKEN)
        run = _runs_repo.create.call_args[0][0]
        assert run["rollout"]["waves"] == [2, 2, 1]

    def test_no_policy_still_waves_everyone(self, _runs_repo):
        # No wave policy -> the platform read default (auto/continue) applies: every member
        # still runs, in waves of the cap. 5 members, max_fanout 2 -> waves [2, 2, 1].
        roster = self._active_roster(5)
        fleet = {**_FLEET, "max_fanout": 2}
        pa, pf, par, pj, agents, _ = _patch(agents=roster, fleet=fleet)
        with pa, pf as fr, par as ar, pj as jr, \
             patch("handlers.cli_fleets.tenants_repo") as tr:
            fr.get.return_value = fleet
            tr.get.return_value = {"tenant_id": TENANT_ID, "settings": {}}
            ar.list_by_fleet.return_value = roster
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)   # read
        body = json.loads(r["body"])
        assert "capped" not in body
        assert body["dispatched"] == 2 and body["total"] == 5 and body["wave_total"] == 3
        run = _runs_repo.create.call_args[0][0]
        assert run["rollout"]["waves"] == [2, 2, 1]
        assert run["rollout"]["mode"] == "auto" and run["rollout"]["on_failure"] == "continue"

    def test_over_cap_waves_everyone(self):
        roster = self._active_roster(30)   # > default cap (25)
        pa, pf, par, pj, agents, fleet = _patch(agents=roster)
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = roster
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)
        body = json.loads(r["body"])
        # 30 members, cap 25 -> waves [25, 5]: 25 run now, all 30 eventually.
        assert body["dispatched"] == 25 and body["total"] == 30 and body["wave_total"] == 2
        assert jr.create.call_count == 30

    def test_fleet_max_fanout_sets_wave_size(self):
        roster = self._active_roster(30)
        fleet = {**_FLEET, "max_fanout": 10}   # tighter per-wave size on the fleet
        pa, pf, par, pj, agents, _ = _patch(agents=roster, fleet=fleet)
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = roster
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)
        body = json.loads(r["body"])
        # 30 members, wave size 10 -> waves [10, 10, 10]: 10 now, all 30 eventually.
        assert body["dispatched"] == 10 and body["total"] == 30 and body["wave_total"] == 3

    def test_max_targets_over_fleet_cap_refused(self):
        roster = self._active_roster(30)
        fleet = {**_FLEET, "max_fanout": 10}
        pa, pf, par, pj, agents, _ = _patch(agents=roster, fleet=fleet)
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = roster
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime", "max_targets": 20}, TOKEN)
        assert r["statusCode"] == 409
        jr.create.assert_not_called()

    def test_max_targets_lowers_wave_size(self):
        roster = self._active_roster(30)
        pa, pf, par, pj, agents, fleet = _patch(agents=roster)
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = roster
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime", "max_targets": 10}, TOKEN)
        body = json.loads(r["body"])
        # max_targets lowers the wave size to 10 -> waves [10, 10, 10]; still all 30 run.
        assert body["dispatched"] == 10 and body["total"] == 30 and body["wave_total"] == 3
        assert jr.create.call_count == 30
        # Wave 0 (dispatched now) is the first 10 by hostname -> web-000..web-009.
        wave0 = [j["hostname"] for j in body["jobs"] if j["status"] == "PENDING"]
        assert wave0 == [f"web-{i:03d}" for i in range(10)]

    def test_dry_run_returns_preview_without_dispatch(self, _runs_repo):
        roster = self._active_roster(5)
        fleet = {**_FLEET, "mode": "approved", "max_fanout": 2,
                 "wave_policy": {"write": {"mode": "manual", "on_failure": "stop"}}}
        pa, pf, par, pj, agents, _ = _patch(agents=roster, fleet=fleet)
        with pa, pf as fr, par as ar, pj as jr, \
             patch("handlers.cli_fleets.tenants_repo") as tr:
            fr.get.return_value = fleet
            tr.get.return_value = {"tenant_id": TENANT_ID, "settings": {}}
            ar.list_by_fleet.return_value = roster
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "rm -rf /tmp/x", "dry_run": True}, TOKEN)
        body = json.loads(r["body"])
        assert body["dry_run"] is True
        assert body["matched"] == 5 and body["wave_size"] == 2 and body["wave_total"] == 3
        assert body["wave_strategy"] == "manual" and body["failure_policy"] == "stop"
        assert body["mode"] == "approved" and body["approval_required"] is True   # write to approved fleet
        jr.create.assert_not_called()
        _runs_repo.create.assert_not_called()

    def test_within_cap_single_wave_dispatches_all(self):
        roster = self._active_roster(20)   # <= cap -> one wave, all at once
        pa, pf, par, pj, agents, fleet = _patch(agents=roster)
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = roster
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)
        body = json.loads(r["body"])
        assert body["dispatched"] == 20 and body["wave_total"] == 1

    def test_invalid_max_targets_400(self):
        pa, pf, par, pj, agents, fleet = _patch(agents=[_M1])
        with pa, pf as fr, par as ar, pj:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = [_M1]
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime", "max_targets": "abc"}, TOKEN)
        assert r["statusCode"] == 400

    def test_idempotency_key_dedupes_replay(self, _runs_repo):
        # A retried fan-out with the same key returns the existing run, no new dispatch.
        _runs_repo.get.return_value = {"run_id": "batch_x", "dispatched": 1,
                                       "state": "running", "counts": {"pending": 1}}
        pa, pf, par, pj, agents, fleet = _patch(agents=[_M1])
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = [_M1]
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime", "idempotency_key": "abc"}, TOKEN)
        body = json.loads(r["body"])
        assert body["deduplicated"] is True and body["dispatched"] == 1
        jr.create.assert_not_called()   # nothing re-dispatched

    def test_idempotency_key_first_call_dispatches(self):
        pa, pf, par, pj, agents, fleet = _patch(agents=[_M1])
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = [_M1]
            jr.list_by_run.return_value = []   # no prior run for this key
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime", "idempotency_key": "abc"}, TOKEN)
        body = json.loads(r["body"])
        assert body.get("deduplicated") is None and body["dispatched"] == 1
        jr.create.assert_called_once()
        # Same key -> deterministic run_id (stable across retries).
        assert body["run_id"].startswith("run_")


class TestListFleetRuns:
    def _run_rows(self):
        return [
            {"run_id": "batch_a", "command": "uptime", "created_at": "2026-06-01T10:00:00Z",
             "tag": None, "state": "partial", "dispatched": 2,
             "counts": {"ok": 1, "failed": 1, "pending": 0, "running": 0}},
            {"run_id": "batch_b", "command": "df -h", "created_at": "2026-06-01T09:00:00Z",
             "tag": None, "state": "running", "dispatched": 1,
             "counts": {"ok": 0, "failed": 0, "pending": 1, "running": 0}},
        ]

    def _call(self, user=_ADMIN, run_rows=None):
        pa, pf, par, pj, _a, fleet = _patch(user=user)
        with pa, pf as fr, par, pj, patch("handlers.cli_fleets.runs_repo") as rr:
            fr.get.return_value = fleet
            rr.list_by_fleet.return_value = run_rows if run_rows is not None else self._run_rows()
            return handle_cli_list_fleet_runs(FLEET_ID, TOKEN), rr

    def test_lists_runs_with_counts_from_runs_table(self):
        r, rr = self._call()
        body = json.loads(r["body"])
        assert r["statusCode"] == 200
        rr.list_by_fleet.assert_called_once()   # reads the runs table, not a job scan
        runs = {run["run_id"]: run for run in body["runs"]}
        assert set(runs) == {"batch_a", "batch_b"}
        assert runs["batch_a"]["members"] == 2 and runs["batch_a"]["ok"] == 1 and runs["batch_a"]["failed"] == 1
        assert runs["batch_b"]["pending"] == 1

    def test_full_page_sets_next_cursor(self):
        # A full page (len == limit) -> next_cursor = the last run's created_at.
        pa, pf, par, pj, _a, fleet = _patch(user=_ADMIN)
        with pa, pf as fr, par, pj, patch("handlers.cli_fleets.runs_repo") as rr:
            fr.get.return_value = fleet
            rows = [{"run_id": f"r{i}", "command": "uptime", "created_at": f"2026-06-01T{i:02d}:00:00Z",
                     "tag": None, "state": "succeeded", "dispatched": 1, "counts": {}} for i in range(2)]
            rr.list_by_fleet.return_value = rows
            r = handle_cli_list_fleet_runs(FLEET_ID, TOKEN, limit=2, cursor="2026-06-02T00:00:00Z")
        body = json.loads(r["body"])
        rr.list_by_fleet.assert_called_once_with(FLEET_ID, limit=2, cursor="2026-06-02T00:00:00Z")
        assert body["next_cursor"] == "2026-06-01T01:00:00Z"

    def test_partial_page_no_next_cursor(self):
        r, _ = self._call()   # 2 rows, default limit 20 -> not a full page
        assert json.loads(r["body"])["next_cursor"] is None

    def test_no_access_404(self):
        r, _ = self._call(user=_NONE)
        assert r["statusCode"] == 404

    def test_readonly_fleet_rejected(self):
        pa, pf, par, pj, agents, fleet = _patch(user=_RO)
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = fleet
            ar.list_by_fleet.return_value = agents
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)
        assert r["statusCode"] == 403
        jr.create.assert_not_called()

    def test_readonly_fleet_rejects_writes_upfront(self):
        # Mode is fleet-level, so a write to a read-only fleet is a fleet-level "no" -
        # rejected upfront (409), not a silent all-members skip.
        ro_fleet = {**_FLEET, "mode": "readonly"}
        ro_members = [{**_M1, "mode": "readonly"}]
        pa, pf, par, pj, agents, fleet = _patch(agents=ro_members, fleet=ro_fleet)
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = ro_fleet
            ar.list_by_fleet.return_value = ro_members
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "rm -rf /tmp/x"}, TOKEN)
        assert r["statusCode"] == 409
        assert "read-only" in json.loads(r["body"])["error"]
        jr.create.assert_not_called()

    def test_readonly_fleet_allows_reads(self):
        ro_fleet = {**_FLEET, "mode": "readonly"}
        ro_members = [{**_M1, "mode": "readonly"}]
        pa, pf, par, pj, agents, fleet = _patch(agents=ro_members, fleet=ro_fleet)
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = ro_fleet
            ar.list_by_fleet.return_value = ro_members
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)
        assert json.loads(r["body"])["dispatched"] == 1   # a read runs fine

    def test_reads_dispatch_in_readonly_mode(self):
        ro_fleet = {**_FLEET, "mode": "readonly"}
        ro_members = [{**_M1, "mode": "readonly"}]
        pa, pf, par, pj, agents, fleet = _patch(agents=ro_members, fleet=ro_fleet)
        with pa, pf as fr, par as ar, pj as jr:
            fr.get.return_value = ro_fleet
            ar.list_by_fleet.return_value = ro_members
            jr.create.return_value = None
            r = handle_cli_fleet_fanout(FLEET_ID, {"command": "uptime"}, TOKEN)
        assert json.loads(r["body"])["dispatched"] == 1

    def test_missing_command_400(self):
        pa, pf, par, pj, agents, fleet = _patch()
        with pa, pf as fr, par, pj:
            fr.get.return_value = fleet
            r = handle_cli_fleet_fanout(FLEET_ID, {}, TOKEN)
        assert r["statusCode"] == 400
