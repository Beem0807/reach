"""Temporary wild mode auto-revert (shared.mode.revert_expired_mode / _fleet_mode)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from shared.mode import mode_duration_expiry, revert_expired_fleet_mode, revert_expired_mode


def _iso(offset_s: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat()


class TestRevertExpiredMode:
    def test_non_wild_is_noop(self):
        a = {"agent_id": "a1", "tenant_id": "t", "mode": "approved"}
        with patch("shared.mode.agents_repo") as ar, patch("shared.mode.audit"):
            out = revert_expired_mode(a)
        ar.update_policy.assert_not_called()
        assert out["mode"] == "approved"

    def test_permanent_wild_is_noop(self):
        a = {"agent_id": "a1", "tenant_id": "t", "mode": "wild"}   # no expiry
        with patch("shared.mode.agents_repo") as ar, patch("shared.mode.audit"):
            out = revert_expired_mode(a)
        ar.update_policy.assert_not_called()
        assert out["mode"] == "wild"

    def test_wild_window_still_open_is_noop(self):
        a = {"agent_id": "a1", "tenant_id": "t", "mode": "wild",
             "mode_expires_at": _iso(3600), "mode_revert_to": "approved"}
        with patch("shared.mode.agents_repo") as ar, patch("shared.mode.audit"):
            out = revert_expired_mode(a)
        ar.update_policy.assert_not_called()
        assert out["mode"] == "wild"

    def test_expired_wild_reverts_and_audits(self):
        a = {"agent_id": "a1", "tenant_id": "t", "mode": "wild", "hostname": "h",
             "mode_expires_at": _iso(-10), "mode_revert_to": "approved"}
        with patch("shared.mode.agents_repo") as ar, patch("shared.mode.audit") as au:
            out = revert_expired_mode(a)
        ar.update_policy.assert_called_once_with("a1", "approved", mode_expires_at=None, mode_revert_to=None)
        au.write.assert_called_once()
        assert au.write.call_args[0][0] == "agent.mode_reverted"
        assert out["mode"] == "approved" and out["mode_expires_at"] is None and out["mode_revert_to"] is None

    def test_expired_wild_defaults_revert_to_readonly(self):
        a = {"agent_id": "a1", "tenant_id": "t", "mode": "wild", "mode_expires_at": _iso(-10)}
        with patch("shared.mode.agents_repo") as ar, patch("shared.mode.audit"):
            out = revert_expired_mode(a)
        ar.update_policy.assert_called_once_with("a1", "readonly", mode_expires_at=None, mode_revert_to=None)
        assert out["mode"] == "readonly"


class TestRevertExpiredFleetMode:
    def test_open_window_is_noop(self):
        f = {"fleet_id": "f1", "tenant_id": "t", "mode": "wild",
             "mode_expires_at": _iso(3600), "mode_revert_to": "approved"}
        with patch("shared.mode.fleets_repo") as fr, patch("shared.mode.agents_repo") as ar, \
             patch("shared.mode.audit"):
            out = revert_expired_fleet_mode(f)
        fr.update_settings.assert_not_called()
        ar.set_mode_by_fleet.assert_not_called()
        assert out["mode"] == "wild"

    def test_expired_reverts_propagates_and_audits(self):
        f = {"fleet_id": "f1", "tenant_id": "t", "name": "web", "mode": "wild",
             "mode_expires_at": _iso(-10), "mode_revert_to": "approved"}
        with patch("shared.mode.fleets_repo") as fr, patch("shared.mode.agents_repo") as ar, \
             patch("shared.mode.audit") as au:
            out = revert_expired_fleet_mode(f)
        # Reverts the fleet AND re-propagates to members (both clearing the schedule).
        fr.update_settings.assert_called_once_with(
            "f1", {"mode": "approved", "mode_expires_at": None, "mode_revert_to": None})
        ar.set_mode_by_fleet.assert_called_once_with(
            "f1", "approved", mode_expires_at=None, mode_revert_to=None)
        assert au.write.call_args[0][0] == "fleet.mode_reverted"
        assert out["mode"] == "approved" and out["mode_expires_at"] is None


class TestModeDurationExpiry:
    def test_permanent_and_empty(self):
        assert mode_duration_expiry("") == (True, None)
        assert mode_duration_expiry("permanent") == (True, None)

    def test_valid_units(self):
        for d in ("1h", "4h", "1d", "1w"):
            ok, exp = mode_duration_expiry(d)
            assert ok and exp is not None

    def test_invalid_and_over_cap(self):
        assert mode_duration_expiry("nonsense")[0] is False
        assert mode_duration_expiry("5x")[0] is False
        assert mode_duration_expiry("31d")[0] is False   # over the 30-day ceiling
        assert mode_duration_expiry("0h")[0] is False
