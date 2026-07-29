"""
Backend half of the backend<->agent enforcement-parity check.

The agent (agent/main.go) re-implements two policy primitives in Go that gate the structured
host-exec approval boundary, and its comments promise they stay "in sync" with the backend's
shared/policy.py. This test - together with its Go twin (agent/enforcement_parity_test.go) - makes
that promise load-bearing: both sides load the SAME golden vectors (policy_parity_vectors.json) and
assert their implementation returns the encoded (server-authoritative) decision. If the Python and
Go implementations ever diverge, at least one of the two suites goes red in CI.

The two mirrored primitives:
  * host_rule_matches(argv, rule)  <->  hostRuleMatches  - which approved rules permit which argv.
  * a structured argv's sensitivity  <->  argvReadsSensitivePath. On the backend that is
    is_sensitive_read(joined) OR is_k8s_secret_read(joined): a structured argv is exactly the shell
    command joined by spaces, and the agent's argvReadsSensitivePath folds the kubectl-Secret-read
    check into the same predicate, so parity is checked against the union.
"""
import json
import os

import pytest

from shared.policy import host_rule_matches, is_k8s_secret_read, is_sensitive_read

_VECTORS = os.path.join(os.path.dirname(__file__), "policy_parity_vectors.json")

with open(_VECTORS) as f:
    _DATA = json.load(f)


def _argv_is_sensitive(argv: list) -> bool:
    """The backend's structured-argv sensitivity, matching the agent's argvReadsSensitivePath:
    a sensitive path read OR a kubectl Secret read. A structured argv is the command joined by
    spaces (it only becomes an argv when it carries no shell metacharacters)."""
    joined = " ".join(argv)
    return is_sensitive_read(joined) or is_k8s_secret_read(joined)


@pytest.mark.parametrize("v", _DATA["host_rule_matches"], ids=lambda v: v["desc"])
def test_host_rule_matches_parity(v):
    assert host_rule_matches(v["argv"], v["rule"]) is v["expected"], (
        f"host_rule_matches drift: {v['desc']} (argv={v['argv']} rule={v['rule']})")


@pytest.mark.parametrize("v", _DATA["sensitive_read"], ids=lambda v: v["desc"])
def test_sensitive_read_parity(v):
    assert _argv_is_sensitive(v["argv"]) is v["expected"], (
        f"sensitive-read drift: {v['desc']} (argv={v['argv']})")


def test_vectors_cover_both_outcomes():
    """Guard against a vacuous contract: each set must exercise both accept and reject."""
    for name in ("host_rule_matches", "sensitive_read"):
        outcomes = {v["expected"] for v in _DATA[name]}
        assert outcomes == {True, False}, f"{name} vectors must include both true and false cases"
