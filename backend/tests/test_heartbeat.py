from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call

from handlers.heartbeat import handle_heartbeat_check


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


class TestHeartbeatApprovalScheduling:
    def _call_at(self, hour, minute, mark_expired_count=0, delete_stale_count=0, retention_days=None):
        fixed_dt = _make_dt(hour=hour, minute=minute)
        fixed_ts = fixed_dt.timestamp()
        env = {}
        if retention_days is not None:
            env["APPROVAL_RETENTION_DAYS"] = str(retention_days)
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo") as apr, \
             patch("handlers.heartbeat._now", return_value=fixed_ts), \
             patch.dict("os.environ", env, clear=False):
            ar.scan_stale_active.return_value = []
            ar.mark_inactive.return_value = True
            jr.expire_stale.return_value = 0
            apr.mark_expired.return_value = mark_expired_count
            apr.delete_stale.return_value = delete_stale_count
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
        assert result["expired_approvals"] == 2
        assert result["deleted_approvals"] == 5

    def test_delete_stale_uses_retention_days_env(self):
        fixed_dt = _make_dt(hour=0, minute=0)
        fixed_ts = fixed_dt.timestamp()
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo") as apr, \
             patch("handlers.heartbeat._now", return_value=fixed_ts), \
             patch.dict("os.environ", {"APPROVAL_RETENTION_DAYS": "14"}, clear=False):
            ar.scan_stale_active.return_value = []
            ar.mark_inactive.return_value = True
            jr.expire_stale.return_value = 0
            apr.mark_expired.return_value = 0
            apr.delete_stale.return_value = 0
            handle_heartbeat_check()
        before_arg = apr.delete_stale.call_args[0][0]
        before_dt = datetime.fromisoformat(before_arg)
        expected_cutoff = fixed_dt - timedelta(days=14)
        diff = abs((before_dt - expected_cutoff).total_seconds())
        assert diff < 2

    def test_delete_stale_defaults_to_7_days(self):
        fixed_dt = _make_dt(hour=0, minute=0)
        fixed_ts = fixed_dt.timestamp()
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr, \
             patch("handlers.heartbeat.approvals_repo") as apr, \
             patch("handlers.heartbeat._now", return_value=fixed_ts), \
             patch.dict("os.environ", {}, clear=False):
            ar.scan_stale_active.return_value = []
            ar.mark_inactive.return_value = True
            jr.expire_stale.return_value = 0
            apr.mark_expired.return_value = 0
            apr.delete_stale.return_value = 0
            # Remove key if present to test default
            import os
            os.environ.pop("APPROVAL_RETENTION_DAYS", None)
            handle_heartbeat_check()
        before_arg = apr.delete_stale.call_args[0][0]
        before_dt = datetime.fromisoformat(before_arg)
        expected_cutoff = fixed_dt - timedelta(days=7)
        diff = abs((before_dt - expected_cutoff).total_seconds())
        assert diff < 2

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
