from shared.fanout import DEFAULT_FANOUT_CAP, parse_max_targets, order_and_limit


def _agents(n):
    return [{"agent_id": f"a{i:03d}", "hostname": f"h{i:03d}"} for i in range(n)]


class TestParseMaxTargets:
    def test_none_passthrough(self):
        assert parse_max_targets(None) == (None, None)

    def test_valid_int(self):
        assert parse_max_targets(5) == (5, None)
        assert parse_max_targets("7") == (7, None)

    def test_non_int_errors(self):
        val, err = parse_max_targets("abc")
        assert val is None and "integer" in err

    def test_below_one_errors(self):
        val, err = parse_max_targets(0)
        assert val is None and ">= 1" in err


class TestOrderAndLimit:
    """order_and_limit orders the eligible targets and resolves the per-wave SIZE (the
    fan-out cap, lowerable by max_targets). Every eligible member runs across waves of
    that size - there is no capping/dropping."""

    def test_wave_size_is_cap(self):
        ordered, wave_size, err = order_and_limit(_agents(30), cap=25)
        assert len(ordered) == 30 and wave_size == 25 and err is None   # all 30 run, 25/wave

    def test_max_targets_lowers_wave_size(self):
        ordered, wave_size, err = order_and_limit(_agents(30), max_targets=10, cap=25)
        assert len(ordered) == 30 and wave_size == 10 and err is None

    def test_max_targets_over_cap_refused(self):
        # max_targets can lower the wave size, never raise it above the cap.
        ordered, wave_size, err = order_and_limit(_agents(40), max_targets=40, cap=25)
        assert wave_size is None and "can't be overridden" in err

    def test_deterministic_order_by_hostname(self):
        shuffled = [{"agent_id": "a3", "hostname": "h3"},
                    {"agent_id": "a1", "hostname": "h1"},
                    {"agent_id": "a2", "hostname": "h2"}]
        ordered, _, _ = order_and_limit(shuffled, cap=DEFAULT_FANOUT_CAP)
        assert [t["hostname"] for t in ordered] == ["h1", "h2", "h3"]
