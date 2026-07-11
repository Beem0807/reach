"""Tests for shared/waves.py - wave sizing (plan_waves), policy validation/resolution,
and advancement honoring mode (auto/manual) + on_failure (stop/continue)."""
from shared.waves import (plan_waves, assign_waves, advance_waves, rollout_meta,
                          validate_wave_strategy, resolve_policy)
from shared.fanout import aggregate_run


class TestPlanWaves:
    def test_no_rollout_is_single_wave(self):
        assert plan_waves(10, None) == ([10], None)
        assert plan_waves(10, {}) == ([10], None)

    def test_batch_repeats(self):
        sizes, err = plan_waves(12, {"batch": 5})
        assert sizes == [5, 5, 2] and err is None

    def test_batch_exact(self):
        sizes, err = plan_waves(10, {"batch": 5})
        assert sizes == [5, 5] and err is None

    def test_batch_bigger_than_total(self):
        sizes, err = plan_waves(3, {"batch": 5})
        assert sizes == [3] and err is None

    def test_canary_then_rest(self):
        sizes, err = plan_waves(100, {"canary": 5})
        assert sizes == [5, 95] and err is None

    def test_explicit_waves_prefix_plus_remainder(self):
        sizes, err = plan_waves(100, {"waves": [5, 20]})
        assert sizes == [5, 20, 75] and err is None

    def test_bad_batch(self):
        assert plan_waves(10, {"batch": 0})[0] == [10]   # 0 batch -> not a size -> single wave
        assert plan_waves(10, {"batch": "x"})[1]

    def test_bad_canary(self):
        assert plan_waves(10, {"canary": "x"})[1]

    def test_too_many_waves_rejected(self):
        assert plan_waves(1000, {"batch": 1})[1]  # 1000 waves > MAX_WAVES

    def test_rollout_with_only_meta_is_single_wave(self):
        # A policy entry (mode/on_failure) with no size => not staged here.
        assert plan_waves(10, {"mode": "manual", "on_failure": "stop"}) == ([10], None)


class TestRolloutMeta:
    def test_defaults(self):
        assert rollout_meta(None) == {"mode": "auto", "on_failure": "stop"}
        assert rollout_meta({}) == {"mode": "auto", "on_failure": "stop"}

    def test_reads_values(self):
        assert rollout_meta({"mode": "manual", "on_failure": "continue"}) == {
            "mode": "manual", "on_failure": "continue"}

    def test_bad_values_fall_back(self):
        assert rollout_meta({"mode": "bogus"})["mode"] == "auto"


class TestValidateWaveStrategy:
    def test_none_and_empty(self):
        assert validate_wave_strategy(None) == (None, None)
        assert validate_wave_strategy({}) == (None, None)

    def test_valid(self):
        clean, err = validate_wave_strategy({"mode": "manual", "on_failure": "continue"})
        assert err is None and clean == {"mode": "manual", "on_failure": "continue"}

    def test_defaults_when_partial(self):
        clean, _ = validate_wave_strategy({"mode": "manual"})
        assert clean == {"mode": "manual", "on_failure": "stop"}
        clean, _ = validate_wave_strategy({"on_failure": "continue"})
        assert clean == {"mode": "auto", "on_failure": "continue"}

    def test_bad_mode(self):
        assert validate_wave_strategy({"mode": "sometimes"})[1]

    def test_bad_on_failure(self):
        assert validate_wave_strategy({"on_failure": "maybe"})[1]

    def test_concurrency_kept(self):
        clean, err = validate_wave_strategy({"mode": "auto", "concurrency": 3})
        assert err is None and clean == {"mode": "auto", "on_failure": "stop", "concurrency": 3}

    def test_concurrency_omitted_when_absent(self):
        clean, _ = validate_wave_strategy({"mode": "auto"})
        assert "concurrency" not in clean

    def test_zero_concurrency_means_unset(self):
        clean, err = validate_wave_strategy({"mode": "auto", "concurrency": 0})
        assert err is None and "concurrency" not in clean   # 0 -> use the cap

    def test_bad_concurrency(self):
        assert validate_wave_strategy({"mode": "auto", "concurrency": -1})[1]
        assert validate_wave_strategy({"mode": "auto", "concurrency": "x"})[1]


class TestResolvePolicy:
    def _tenant(self, wave_policy):
        return {"settings": {"wave_policy": wave_policy}}

    def test_platform_defaults_when_unset(self):
        # Unset -> the platform default per read/write: read auto/continue, write manual/stop.
        assert resolve_policy(True, self._tenant({}), "fleet") == {"mode": "manual", "on_failure": "stop"}
        assert resolve_policy(False, self._tenant({}), "fleet") == {"mode": "auto", "on_failure": "continue"}
        assert resolve_policy(True, {}, "tag") == {"mode": "manual", "on_failure": "stop"}
        assert resolve_policy(False, {}, "tag") == {"mode": "auto", "on_failure": "continue"}

    def test_tenant_tag_write(self):
        t = self._tenant({"tag": {"write": {"mode": "manual", "on_failure": "stop"}}})
        assert resolve_policy(True, t, "tag") == {"mode": "manual", "on_failure": "stop"}
        # Read unset -> the platform read default (not the write override).
        assert resolve_policy(False, t, "tag") == {"mode": "auto", "on_failure": "continue"}

    def test_tenant_fleet_default(self):
        t = self._tenant({"fleet": {"write": {"mode": "auto", "on_failure": "continue"}}})
        assert resolve_policy(True, t, "fleet") == {"mode": "auto", "on_failure": "continue"}

    def test_fleet_override_beats_tenant(self):
        t = self._tenant({"fleet": {"write": {"mode": "auto", "on_failure": "stop"}}})
        fleet = {"wave_policy": {"write": {"mode": "manual", "on_failure": "continue"}}}
        assert resolve_policy(True, t, "fleet", fleet) == {"mode": "manual", "on_failure": "continue"}

    def test_fleet_falls_back_to_tenant_when_branch_unset(self):
        t = self._tenant({"fleet": {"write": {"mode": "manual", "on_failure": "stop"}}})
        fleet = {"wave_policy": {"read": {"mode": "auto", "on_failure": "stop"}}}  # only read set
        assert resolve_policy(True, t, "fleet", fleet) == {"mode": "manual", "on_failure": "stop"}


class TestAssignWaves:
    def test_assigns_in_order(self):
        targets = [{"agent_id": f"a{i}"} for i in range(5)]
        pairs = assign_waves(targets, [2, 3])
        assert [w for _, w in pairs] == [0, 0, 1, 1, 1]


def _job(wave, status, exit_code=0):
    return {"wave": wave, "status": status, "exit_code": exit_code}


class TestAdvanceWaves:
    def _run(self, cw=0, wt=3, state="running", mode="auto", on_failure="stop"):
        return {"state": state, "current_wave": cw, "wave_total": wt,
                "rollout": {"waves": [2, 2, 1], "mode": mode, "on_failure": on_failure}}

    def test_current_wave_in_flight_stays_running(self):
        jobs = [_job(0, "RUNNING"), _job(1, "HELD")]
        d = advance_waves(self._run(), jobs, aggregate_run(jobs))
        assert d == {"state": "running", "current_wave": 0, "release_wave": None}

    def test_auto_clean_wave_releases_next(self):
        jobs = [_job(0, "SUCCEEDED"), _job(1, "HELD")]
        d = advance_waves(self._run(cw=0, wt=2, mode="auto"), jobs, aggregate_run(jobs))
        assert d["release_wave"] == 1 and d["current_wave"] == 1 and d["state"] == "running"

    def test_manual_pauses_after_clean_wave(self):
        jobs = [_job(0, "SUCCEEDED"), _job(1, "HELD")]
        d = advance_waves(self._run(cw=0, wt=2, mode="manual"), jobs, aggregate_run(jobs))
        assert d["state"] == "paused" and d["release_wave"] is None

    def test_stop_pauses_on_failure(self):
        jobs = [_job(0, "FAILED", exit_code=1), _job(1, "HELD")]
        d = advance_waves(self._run(cw=0, wt=2, mode="auto", on_failure="stop"), jobs, aggregate_run(jobs))
        assert d["state"] == "paused" and d["release_wave"] is None

    def test_continue_advances_despite_failure(self):
        jobs = [_job(0, "FAILED", exit_code=1), _job(1, "HELD")]
        d = advance_waves(self._run(cw=0, wt=2, mode="auto", on_failure="continue"), jobs, aggregate_run(jobs))
        assert d["release_wave"] == 1 and d["state"] == "running"

    def test_manual_pauses_even_on_continue(self):
        jobs = [_job(0, "FAILED", exit_code=1), _job(1, "HELD")]
        d = advance_waves(self._run(cw=0, wt=2, mode="manual", on_failure="continue"), jobs, aggregate_run(jobs))
        assert d["state"] == "paused"

    def test_last_wave_reaches_terminal(self):
        jobs = [_job(0, "SUCCEEDED"), _job(1, "SUCCEEDED")]
        d = advance_waves(self._run(cw=1, wt=2), jobs, aggregate_run(jobs))
        assert d["state"] == "succeeded" and d["release_wave"] is None

    def test_paused_run_not_auto_advanced(self):
        jobs = [_job(0, "SUCCEEDED"), _job(1, "HELD")]
        d = advance_waves(self._run(cw=0, wt=2, state="paused"), jobs, aggregate_run(jobs))
        assert d["state"] == "paused" and d["release_wave"] is None

    def test_canceled_run_stays_canceled(self):
        jobs = [_job(0, "SUCCEEDED"), _job(1, "CANCELED")]
        d = advance_waves(self._run(cw=0, wt=2, state="canceled"), jobs, aggregate_run(jobs))
        assert d["state"] == "canceled"


class TestAggregateWithHeldAndCanceled:
    def test_held_counts_as_pending(self):
        agg = aggregate_run([_job(0, "SUCCEEDED"), _job(1, "HELD")])
        assert agg["counts"]["pending"] == 1 and agg["terminal"] is False

    def test_canceled_ignored_not_failed(self):
        agg = aggregate_run([_job(0, "SUCCEEDED"), _job(1, "CANCELED")])
        assert agg["counts"]["failed"] == 0 and agg["terminal"] is True
        assert agg["state"] == "succeeded"
