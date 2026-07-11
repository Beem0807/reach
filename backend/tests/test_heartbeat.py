from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call

from handlers.heartbeat import handle_heartbeat_check, heartbeat_handler


def _make_dt(hour=12, minute=30):
    """Return a fixed UTC datetime for time-branching tests."""
    return datetime(2025, 1, 15, hour, minute, 0, tzinfo=timezone.utc)


class TestHeartbeatCheck:
    def _call(self, stale_agents=None, expired_jobs=0, now_dt=None):
        fixed_dt = now_dt or _make_dt()
        fixed_ts = fixed_dt.timestamp()
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo") as apr, \
             patch("handlers.heartbeat._now", return_value=fixed_ts):
            ar.scan_stale_active.return_value = stale_agents or []
            ar.mark_inactive.return_value = True
            jr.expire_stale.return_value = expired_jobs
            apr.mark_expired.return_value = 0
            apr.delete_stale.return_value = 0
            result = handle_heartbeat_check()
            return result, ar, jr, apr

    def test_no_stale_agents(self):
        result, _, _, _ = self._call()
        assert result["marked_inactive"] == 0
        assert result["expired_jobs"] == 0

    def test_marks_stale_agents_inactive(self):
        stale = [{"agent_id": "agent_a"}, {"agent_id": "agent_b"}]
        result, ar, _, _ = self._call(stale_agents=stale)
        assert result["marked_inactive"] == 2
        assert ar.mark_inactive.call_count == 2

    def test_expired_jobs_reported(self):
        result, _, jr, _ = self._call(expired_jobs=5)
        assert result["expired_jobs"] == 5
        jr.expire_stale.assert_called_once()

    def test_mark_inactive_returns_false_not_counted(self):
        stale = [{"agent_id": "agent_a"}]
        fixed_ts = _make_dt().timestamp()
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo"), \
             patch("handlers.heartbeat._now", return_value=fixed_ts):
            ar.scan_stale_active.return_value = stale
            ar.mark_inactive.return_value = False  # concurrent update won
            jr.expire_stale.return_value = 0
            result = handle_heartbeat_check()
        assert result["marked_inactive"] == 0

    def test_scan_uses_45s_cutoff(self):
        fixed_ts = _make_dt().timestamp()
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo"), \
             patch("handlers.heartbeat._now", return_value=fixed_ts):
            ar.scan_stale_active.return_value = []
            jr.expire_stale.return_value = 0
            handle_heartbeat_check()
        cutoff_arg = ar.scan_stale_active.call_args[0][0]
        cutoff_dt = datetime.fromisoformat(cutoff_arg)
        fixed_dt = datetime.fromtimestamp(fixed_ts, tz=timezone.utc)
        age = (fixed_dt - cutoff_dt).total_seconds()
        assert 44 < age < 46


class TestHeartbeatFleetReaper:
    """The reaper removes fleet members that stopped heartbeating past their
    fleet's reap window."""

    def _call(self, fleets, reapable_members, now_dt=None):
        fixed_dt = now_dt or _make_dt()
        fixed_ts = fixed_dt.timestamp()
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.fleets_repo") as fr, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo") as apr, \
             patch("handlers.heartbeat.audit_repo"), \
             patch("handlers.heartbeat.agent_history_repo") as ahr, \
             patch("handlers.heartbeat.audit") as aud, \
             patch("handlers.heartbeat._now", return_value=fixed_ts):
            ar.scan_stale_active.return_value = []
            ar.mark_inactive.return_value = True
            ar.scan_reapable_fleet_members.return_value = reapable_members
            fr.scan_all.return_value = fleets
            jr.expire_stale.return_value = 0
            apr.mark_expired.return_value = 0
            apr.delete_stale.return_value = 0
            result = handle_heartbeat_check()
            return result, ar, ahr, aud, fixed_ts

    def _iso_ago(self, now_ts, secs):
        return datetime.fromtimestamp(now_ts - secs, tz=timezone.utc).isoformat()

    def test_no_fleets_skips_reaper(self):
        result, ar, _, _, _ = self._call(fleets=[], reapable_members=None)
        assert result["reaped_members"] == 0
        ar.scan_reapable_fleet_members.assert_not_called()

    def test_reaps_member_past_window(self):
        now_ts = _make_dt().timestamp()
        member = {"agent_id": "agent_z", "tenant_id": "t1", "fleet_id": "fleet_1",
                  "status": "INACTIVE", "hostname": "ip-10-0-0-5",
                  "last_heartbeat_at": self._iso_ago(now_ts, 700)}
        fleets = [{"fleet_id": "fleet_1", "name": "web-prod", "reap_after_seconds": 600}]
        result, ar, ahr, aud, _ = self._call(fleets=fleets, reapable_members=[member])
        assert result["reaped_members"] == 1
        ar.delete.assert_called_once_with("agent_z")
        # history entry written before deletion, marking DELETED by the reaper
        hist = ahr.create.call_args[0][0]
        assert hist["agent_id"] == "agent_z"
        assert hist["to_status"] == "DELETED"
        assert hist["triggered_by"] == "reaper"
        aud.write.assert_called_once()
        assert aud.write.call_args[0][0] == "agent.reaped"

    def test_does_not_reap_within_window(self):
        now_ts = _make_dt().timestamp()
        member = {"agent_id": "agent_y", "tenant_id": "t1", "fleet_id": "fleet_1",
                  "status": "INACTIVE", "last_heartbeat_at": self._iso_ago(now_ts, 300)}
        fleets = [{"fleet_id": "fleet_1", "name": "web-prod", "reap_after_seconds": 600}]
        result, ar, _, _, _ = self._call(fleets=fleets, reapable_members=[member])
        assert result["reaped_members"] == 0
        ar.delete.assert_not_called()

    def test_uses_default_window_when_fleet_unset(self):
        now_ts = _make_dt().timestamp()
        # 2000s ago > default 1800s window
        member = {"agent_id": "agent_d", "tenant_id": "t1", "fleet_id": "fleet_2",
                  "status": "INACTIVE", "last_heartbeat_at": self._iso_ago(now_ts, 2000)}
        fleets = [{"fleet_id": "fleet_2", "name": "worker", "reap_after_seconds": None}]
        result, ar, _, _, _ = self._call(fleets=fleets, reapable_members=[member])
        assert result["reaped_members"] == 1
        ar.delete.assert_called_once_with("agent_d")

    def test_member_of_unknown_fleet_skipped(self):
        now_ts = _make_dt().timestamp()
        member = {"agent_id": "agent_o", "tenant_id": "t1", "fleet_id": "gone",
                  "status": "INACTIVE", "last_heartbeat_at": self._iso_ago(now_ts, 9999)}
        fleets = [{"fleet_id": "fleet_1", "name": "web-prod", "reap_after_seconds": 600}]
        result, ar, _, _, _ = self._call(fleets=fleets, reapable_members=[member])
        assert result["reaped_members"] == 0
        ar.delete.assert_not_called()

    def test_scan_cutoff_uses_smallest_window(self):
        now_ts = _make_dt().timestamp()
        fleets = [
            {"fleet_id": "f_a", "name": "a", "reap_after_seconds": 600},
            {"fleet_id": "f_b", "name": "b", "reap_after_seconds": 120},
        ]
        _, ar, _, _, fixed_ts = self._call(fleets=fleets, reapable_members=[])
        cutoff_arg = ar.scan_reapable_fleet_members.call_args[0][0]
        cutoff_dt = datetime.fromisoformat(cutoff_arg)
        age = (datetime.fromtimestamp(fixed_ts, tz=timezone.utc) - cutoff_dt).total_seconds()
        assert 119 < age < 121  # smallest window (120s)


class TestHeartbeatApprovalScheduling:
    def _call_at(self, hour, minute, mark_expired_count=0, delete_stale_count=0,
                 tenants=None, tenant_settings=None):
        """Retention is per-tenant now: the midnight sweep iterates tenants_repo.list_all()
        and scopes each delete_stale to the tenant with its own settings."""
        fixed_dt = _make_dt(hour=hour, minute=minute)
        fixed_ts = fixed_dt.timestamp()
        if tenants is None:
            tenants = [{"tenant_id": "t1", "settings": tenant_settings or {}}]
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo") as apr, \
             patch("handlers.heartbeat.runs_repo") as rr, \
             patch("handlers.heartbeat.audit_repo") as audr, \
             patch("handlers.heartbeat.agent_history_repo") as ahr, \
             patch("handlers.heartbeat.tenants_repo") as tr, \
             patch("handlers.heartbeat._now", return_value=fixed_ts):
            ar.scan_stale_active.return_value = []
            ar.mark_inactive.return_value = True
            jr.expire_stale.return_value = 0
            jr.delete_stale.return_value = 0
            rr.delete_stale.return_value = 0
            audr.delete_stale.return_value = 0
            ahr.delete_stale.return_value = 0
            apr.mark_expired.return_value = mark_expired_count
            apr.delete_stale.return_value = delete_stale_count
            tr.list_all.return_value = tenants
            result = handle_heartbeat_check()
            return result, apr

    def test_mid_hour_does_not_call_mark_expired(self):
        result, apr = self._call_at(hour=12, minute=30)
        apr.mark_expired.assert_not_called()
        apr.delete_stale.assert_not_called()
        assert result["expired_approvals"] == 0
        assert result["deleted_approvals"] == 0

    def test_top_of_hour_calls_mark_expired(self):
        result, apr = self._call_at(hour=12, minute=0, mark_expired_count=3)
        apr.mark_expired.assert_called_once()
        assert result["expired_approvals"] == 3

    def test_top_of_hour_does_not_delete_stale(self):
        _, apr = self._call_at(hour=12, minute=0)
        apr.delete_stale.assert_not_called()

    def test_midnight_calls_both_mark_expired_and_delete_stale(self):
        result, apr = self._call_at(hour=0, minute=0, mark_expired_count=2, delete_stale_count=5)
        apr.mark_expired.assert_called_once()
        apr.delete_stale.assert_called_once()
        # Scoped to the tenant.
        assert apr.delete_stale.call_args.kwargs.get("tenant_id") == "t1"
        assert result["expired_approvals"] == 2
        assert result["deleted_approvals"] == 5

    def test_delete_stale_uses_tenant_retention_setting(self):
        _, apr = self._call_at(hour=0, minute=0, tenant_settings={"approval_retention_days": 14})
        before_arg = apr.delete_stale.call_args[0][0]
        before_dt = datetime.fromisoformat(before_arg)
        expected_cutoff = _make_dt(hour=0, minute=0) - timedelta(days=14)
        assert abs((before_dt - expected_cutoff).total_seconds()) < 2

    def test_delete_stale_defaults_to_7_days(self):
        _, apr = self._call_at(hour=0, minute=0, tenant_settings={})
        before_arg = apr.delete_stale.call_args[0][0]
        before_dt = datetime.fromisoformat(before_arg)
        expected_cutoff = _make_dt(hour=0, minute=0) - timedelta(days=7)
        assert abs((before_dt - expected_cutoff).total_seconds()) < 2

    def test_mark_expired_called_with_current_iso(self):
        fixed_dt = _make_dt(hour=3, minute=0)
        fixed_ts = fixed_dt.timestamp()
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo") as apr, \
             patch("handlers.heartbeat._now", return_value=fixed_ts):
            ar.scan_stale_active.return_value = []
            ar.mark_inactive.return_value = True
            jr.expire_stale.return_value = 0
            apr.mark_expired.return_value = 0
            handle_heartbeat_check()
        now_arg = apr.mark_expired.call_args[0][0]
        now_dt = datetime.fromisoformat(now_arg)
        diff = abs((now_dt - fixed_dt).total_seconds())
        assert diff < 2


class TestHeartbeatCleanupExtended:
    """Per-tenant retention for jobs, runs, tenant-audit and agent history, plus the
    platform-level audit trail (tenant_id IS NULL) which keeps its own env window."""

    def _call_midnight(self, env=None, tenant_settings=None, audr_returns=None,
                       ahr_returns=0, runs_returns=0):
        fixed_dt = _make_dt(hour=0, minute=0)
        fixed_ts = fixed_dt.timestamp()
        tenant = {"tenant_id": "t1", "settings": tenant_settings or {}}
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo") as apr, \
             patch("handlers.heartbeat.audit_repo") as audr, \
             patch("handlers.heartbeat.agent_history_repo") as ahr, \
             patch("handlers.heartbeat.runs_repo") as rr, \
             patch("handlers.heartbeat.tenants_repo") as tr, \
             patch("handlers.heartbeat._now", return_value=fixed_ts), \
             patch.dict("os.environ", env or {}, clear=False):
            ar.scan_stale_active.return_value = []
            ar.mark_inactive.return_value = True
            jr.expire_stale.return_value = 0
            apr.mark_expired.return_value = 0
            apr.delete_stale.return_value = 0
            jr.delete_stale.return_value = 0
            rr.delete_stale.return_value = runs_returns
            if audr_returns is not None:
                audr.delete_stale.side_effect = audr_returns  # [tenant_scope, platform_scope]
            else:
                audr.delete_stale.return_value = 0
            ahr.delete_stale.return_value = ahr_returns
            tr.list_all.return_value = [tenant]
            result = handle_heartbeat_check()
            return result, fixed_dt, audr, ahr, rr

    def test_midnight_calls_audit_delete_stale(self):
        _, _, audr, _, _ = self._call_midnight()
        # Called twice: once tenant-scoped, once platform-only.
        assert audr.delete_stale.call_count == 2

    def test_midnight_calls_agent_history_delete_stale(self):
        _, _, _, ahr, _ = self._call_midnight()
        ahr.delete_stale.assert_called_once()
        assert ahr.delete_stale.call_args.kwargs.get("tenant_id") == "t1"

    def test_midnight_calls_runs_delete_stale(self):
        _, _, _, _, rr = self._call_midnight(runs_returns=4)
        rr.delete_stale.assert_called_once()
        assert rr.delete_stale.call_args.kwargs.get("tenant_id") == "t1"

    def test_result_includes_deleted_counts(self):
        # tenant audit (7) + platform audit (2) = 9 total; history 3; runs 4.
        result, _, _, _, _ = self._call_midnight(
            audr_returns=[7, 2], ahr_returns=3, runs_returns=4)
        assert result["deleted_audit_logs"] == 9
        assert result["deleted_agent_history"] == 3
        assert result["deleted_runs"] == 4

    def test_tenant_audit_uses_tenant_setting(self):
        _, fixed_dt, audr, _, _ = self._call_midnight(
            tenant_settings={"audit_retention_days": 45})
        # The tenant-scoped call (has tenant_id kwarg) uses the tenant's setting.
        tenant_call = next(c for c in audr.delete_stale.call_args_list
                           if c.kwargs.get("tenant_id") == "t1")
        before_dt = datetime.fromisoformat(tenant_call.args[0])
        expected = fixed_dt - timedelta(days=45)
        assert abs((before_dt - expected).total_seconds()) < 2

    def test_platform_audit_uses_env(self):
        _, fixed_dt, audr, _, _ = self._call_midnight(env={"AUDIT_RETENTION_DAYS": "30"})
        platform_call = next(c for c in audr.delete_stale.call_args_list
                             if c.kwargs.get("platform_only"))
        before_dt = datetime.fromisoformat(platform_call.args[0])
        expected = fixed_dt - timedelta(days=30)
        assert abs((before_dt - expected).total_seconds()) < 2

    def test_platform_audit_defaults_to_90_days(self):
        import os
        os.environ.pop("AUDIT_RETENTION_DAYS", None)
        _, fixed_dt, audr, _, _ = self._call_midnight()
        platform_call = next(c for c in audr.delete_stale.call_args_list
                             if c.kwargs.get("platform_only"))
        before_dt = datetime.fromisoformat(platform_call.args[0])
        expected = fixed_dt - timedelta(days=90)
        assert abs((before_dt - expected).total_seconds()) < 2

    def test_agent_history_retention_uses_tenant_setting(self):
        _, fixed_dt, _, ahr, _ = self._call_midnight(
            tenant_settings={"agent_history_retention_days": 14})
        before_dt = datetime.fromisoformat(ahr.delete_stale.call_args.args[0])
        expected = fixed_dt - timedelta(days=14)
        assert abs((before_dt - expected).total_seconds()) < 2

    def test_agent_history_retention_defaults_to_30_days(self):
        _, fixed_dt, _, ahr, _ = self._call_midnight(tenant_settings={})
        before_dt = datetime.fromisoformat(ahr.delete_stale.call_args.args[0])
        expected = fixed_dt - timedelta(days=30)
        assert abs((before_dt - expected).total_seconds()) < 2

    def test_non_midnight_does_not_cleanup_audit(self):
        fixed_dt = _make_dt(hour=12, minute=0)
        fixed_ts = fixed_dt.timestamp()
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo") as apr, \
             patch("handlers.heartbeat.audit_repo") as audr, \
             patch("handlers.heartbeat.agent_history_repo") as ahr, \
             patch("handlers.heartbeat._now", return_value=fixed_ts):
            ar.scan_stale_active.return_value = []
            ar.mark_inactive.return_value = True
            jr.expire_stale.return_value = 0
            apr.mark_expired.return_value = 0
            result = handle_heartbeat_check()
        audr.delete_stale.assert_not_called()
        ahr.delete_stale.assert_not_called()
        assert result["deleted_audit_logs"] == 0
        assert result["deleted_agent_history"] == 0

    def test_mark_inactive_writes_agent_history(self):
        stale = [{"agent_id": "agent_x", "tenant_id": "t1", "last_heartbeat_at": "2026-01-01T00:00:00"}]
        fixed_dt = _make_dt()
        fixed_ts = fixed_dt.timestamp()
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo") as apr, \
             patch("handlers.heartbeat.audit_repo") as audr, \
             patch("handlers.heartbeat.agent_history_repo") as ahr, \
             patch("handlers.heartbeat._now", return_value=fixed_ts):
            ar.scan_stale_active.return_value = stale
            ar.mark_inactive.return_value = True
            jr.expire_stale.return_value = 0
            apr.mark_expired.return_value = 0
            handle_heartbeat_check()
        ahr.create.assert_called_once()
        call_args = ahr.create.call_args[0][0]
        assert call_args["agent_id"] == "agent_x"
        assert call_args["from_status"] == "ACTIVE"
        assert call_args["to_status"] == "INACTIVE"
        assert call_args["triggered_by"] == "heartbeat"
