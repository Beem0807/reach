from unittest.mock import patch, MagicMock

from handlers.heartbeat import handle_heartbeat_check


class TestHeartbeatCheck:
    def _call(self, stale_agents=None, expired_jobs=0):
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr:
            ar.scan_stale_active.return_value = stale_agents or []
            ar.mark_inactive.return_value = True
            jr.expire_stale.return_value = expired_jobs
            return handle_heartbeat_check(), ar, jr

    def test_no_stale_agents(self):
        result, _, _ = self._call()
        assert result["marked_inactive"] == 0
        assert result["expired_jobs"] == 0

    def test_marks_stale_agents_inactive(self):
        stale = [{"agent_id": "agent_a"}, {"agent_id": "agent_b"}]
        result, ar, _ = self._call(stale_agents=stale)
        assert result["marked_inactive"] == 2
        assert ar.mark_inactive.call_count == 2

    def test_expired_jobs_reported(self):
        result, _, jr = self._call(expired_jobs=5)
        assert result["expired_jobs"] == 5
        jr.expire_stale.assert_called_once()

    def test_mark_inactive_returns_false_not_counted(self):
        stale = [{"agent_id": "agent_a"}]
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr:
            ar.scan_stale_active.return_value = stale
            ar.mark_inactive.return_value = False  # concurrent update won
            jr.expire_stale.return_value = 0
            result = handle_heartbeat_check()
        assert result["marked_inactive"] == 0

    def test_scan_uses_45s_cutoff(self):
        with patch("handlers.heartbeat.agents_repo") as ar, \
             patch("handlers.heartbeat.jobs_repo") as jr:
            ar.scan_stale_active.return_value = []
            jr.expire_stale.return_value = 0
            handle_heartbeat_check()
        cutoff_arg = ar.scan_stale_active.call_args[0][0]
        # Cutoff should be an ISO string roughly 45s in the past (3 × 15s idle poll)
        from datetime import datetime, timezone
        cutoff_dt = datetime.fromisoformat(cutoff_arg)
        age = (datetime.now(tz=timezone.utc) - cutoff_dt).total_seconds()
        assert 40 < age < 55
