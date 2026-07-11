"""
Tests for reach/mcp_server.py (the MCP server exposing reach tools to Claude).

Strategy:
  - The real `mcp` SDK is an optional/heavy dependency that may not be installed
    in the test environment, so we stub `mcp.server.fastmcp.FastMCP` before import.
    The stub's `.tool()` decorator returns the function unchanged, so each tool is
    callable directly as a plain function.
  - cfg_module and ReachClient are patched so tests never touch ~/.reach or the network.
"""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
import requests

# ---------------------------------------------------------------------------
# Stub the FastMCP SDK so importing mcp_server works without the dependency.
# ---------------------------------------------------------------------------
if "mcp" not in sys.modules:
    class _FakeFastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self, *args, **kwargs):
            def deco(fn):
                return fn
            return deco

        def run(self):
            pass

    _fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    _fastmcp_mod.FastMCP = _FakeFastMCP
    _server_mod = types.ModuleType("mcp.server")
    _server_mod.fastmcp = _fastmcp_mod
    _root = types.ModuleType("mcp")
    _root.server = _server_mod
    sys.modules["mcp"] = _root
    sys.modules["mcp.server"] = _server_mod
    sys.modules["mcp.server.fastmcp"] = _fastmcp_mod

from reach import mcp_server as m  # noqa: E402


# ---------------------------------------------------------------------------
# _redact
# ---------------------------------------------------------------------------
class TestRedact:
    def test_none_passes_through(self):
        assert m._redact(None) is None

    def test_empty_string_passes_through(self):
        assert m._redact("") == ""

    def test_plain_text_unchanged(self):
        assert m._redact("hello world") == "hello world"

    def test_redacts_aws_key_id(self):
        key = "AKIA" + "ABCDEFGHIJKLMNOP"  # split so secret scanners don't flag the literal
        out = m._redact(f"key is {key} here")
        assert "AKIA" not in out
        assert "[AWS_KEY_ID]" in out

    def test_redacts_jwt(self):
        jwt = "eyJhbGciOiJI.eyJzdWIiOiIxMjM.SflKxwRJSMeKKF2QT4"
        out = m._redact(f"token={jwt}")
        assert "[JWT_REDACTED]" in out

    def test_redacts_bearer_token(self):
        out = m._redact("Authorization: Bearer abc123DEF456")
        assert "[TOKEN_REDACTED]" in out
        assert "abc123DEF456" not in out

    def test_redacts_password_assignment(self):
        out = m._redact("password=hunter2supersecret")
        assert "hunter2supersecret" not in out
        assert "[REDACTED]" in out

    def test_redacts_connection_string_credentials(self):
        out = m._redact("postgres://user:p4ssw0rd@db.example.com/app")
        assert "p4ssw0rd" not in out
        assert "[CREDENTIALS_REDACTED]" in out


# ---------------------------------------------------------------------------
# _client
# ---------------------------------------------------------------------------
class TestClient:
    def test_raises_when_not_configured(self):
        with patch.object(m.cfg_module, "load", return_value={}):
            with pytest.raises(RuntimeError, match="not configured"):
                m._client()

    def test_returns_client_and_default_agent(self):
        cfg = {"api_url": "https://api.example.com", "tenant_token": "tok", "default_agent_id": "agent_a"}
        with patch.object(m.cfg_module, "load", return_value=cfg), \
             patch.object(m, "ReachClient") as RC:
            client, default_agent = m._client()
        assert default_agent == "agent_a"
        RC.assert_called_once_with("https://api.example.com", "tok")
        assert client is RC.return_value

    def test_missing_token_raises(self):
        with patch.object(m.cfg_module, "load", return_value={"api_url": "u"}):
            with pytest.raises(RuntimeError):
                m._client()


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------
def _mock_client(default_agent=""):
    """Patch m._client to return a fresh MagicMock client + default agent."""
    client = MagicMock()
    return client, patch.object(m, "_client", return_value=(client, default_agent))


class TestWhoami:
    def test_returns_get_me(self):
        client, p = _mock_client()
        client.get_me.return_value = {"user_id": "user_1"}
        with p:
            assert m.whoami() == {"user_id": "user_1"}


class TestListAgents:
    def test_delegates(self):
        client, p = _mock_client()
        client.list_agents.return_value = {"agents": []}
        with p:
            assert m.list_agents() == {"agents": []}


class TestGetAgent:
    def test_resolves_alias_then_fetches(self):
        client, p = _mock_client()
        client.get_agent.return_value = {"agent_id": "agent_real"}
        with p, patch.object(m.cfg_module, "resolve_agent", return_value="agent_real") as ra:
            r = m.get_agent("prod")
        ra.assert_called_once_with("prod")
        client.get_agent.assert_called_once_with("agent_real")
        assert r == {"agent_id": "agent_real"}


class TestGetContext:
    def test_without_default_agent(self):
        client, p = _mock_client(default_agent="")
        client.get_me.return_value = {"user_id": "user_1"}
        with p, patch.object(m.cfg_module, "load_profile", return_value={"aliases": {"prod": "agent_a"}}):
            r = m.get_context()
        assert r["user"] == {"user_id": "user_1"}
        assert r["default_agent_id"] is None
        assert r["aliases"] == {"prod": "agent_a"}
        assert "default_agent" not in r

    def test_with_default_agent_includes_details(self):
        client, p = _mock_client(default_agent="agent_a")
        client.get_me.return_value = {"user_id": "user_1"}
        client.get_agent.return_value = {
            "agent_id": "agent_a", "status": "ACTIVE", "hostname": "h",
            "mode": "wild", "access_level": "open", "tags": ["x"],
        }
        with p, patch.object(m.cfg_module, "load_profile", return_value={}):
            r = m.get_context()
        assert r["default_agent_id"] == "agent_a"
        assert r["default_agent"]["hostname"] == "h"
        assert r["default_agent"]["tags"] == ["x"]

    def test_default_agent_fetch_failure_is_handled(self):
        client, p = _mock_client(default_agent="agent_a")
        client.get_me.return_value = {}
        client.get_agent.side_effect = Exception("boom")
        with p, patch.object(m.cfg_module, "load_profile", return_value={}):
            r = m.get_context()
        assert "error" in r["default_agent"]


class TestExecCommand:
    def test_errors_when_no_agent_and_no_default(self):
        client, p = _mock_client(default_agent="")
        with p:
            r = m.exec_command("ls", agent_id="")
        assert "error" in r
        client.create_job.assert_not_called()

    def test_runs_and_redacts_output(self):
        client, p = _mock_client(default_agent="agent_a")
        client.create_job.return_value = {"job_id": "job_1"}
        client.get_job.return_value = {
            "status": "SUCCEEDED", "exit_code": 0,
            "stdout": "password=topsecretvalue", "stderr": "",
        }
        with p:
            r = m.exec_command("env", agent_id="agent_a")
        assert r["status"] == "SUCCEEDED"
        assert r["job_id"] == "job_1"
        assert "topsecretvalue" not in r["stdout"]
        # A read runs straight through (dry-run pre-check reports is_write False) and dispatches.
        client.create_job.assert_any_call("agent_a", "env")

    def test_resolves_alias_for_agent(self):
        client, p = _mock_client(default_agent="")
        client.create_job.return_value = {"job_id": "job_2"}
        client.get_job.return_value = {"status": "FAILED", "exit_code": 1, "stdout": "", "stderr": "nope"}
        with p, patch.object(m.cfg_module, "resolve_agent", return_value="agent_real") as ra:
            r = m.exec_command("ls", agent_id="prod")
        ra.assert_called_once_with("prod")
        assert r["agent_id"] == "agent_real"

    def test_polls_until_terminal(self):
        client, p = _mock_client(default_agent="agent_a")
        client.create_job.return_value = {"job_id": "job_1"}
        client.get_job.side_effect = [
            {"status": "RUNNING"},
            {"status": "SUCCEEDED", "exit_code": 0, "stdout": "ok", "stderr": ""},
        ]
        with p, patch.object(m.time, "sleep") as sleep:
            r = m.exec_command("sleep 1", agent_id="agent_a", timeout=60)
        assert r["status"] == "SUCCEEDED"
        assert client.get_job.call_count == 2
        sleep.assert_called()  # waited between polls

    def test_times_out_and_returns_pending(self):
        client, p = _mock_client(default_agent="agent_a")
        client.create_job.return_value = {"job_id": "job_1"}
        # monotonic: first call (deadline base), second call already past deadline.
        with p, patch.object(m.time, "monotonic", side_effect=[1000.0, 2000.0]):
            r = m.exec_command("hang", agent_id="agent_a", timeout=5)
        assert r["status"] == "PENDING"
        assert "Timed out" in r["error"]
        client.get_job.assert_not_called()


class TestExecCommandConfirmGate:
    """Single-agent writes are confirm-gated (symmetry with fleet_exec / exec_by_tag)."""

    def _write_client(self):
        client, p = _mock_client(default_agent="agent_a")

        def _create(agent_id, command, dry_run=False):
            if dry_run:
                return {"dry_run": True, "is_write": True, "hostname": "web-01",
                        "mode": "wild", "agent_id": agent_id, "command": command,
                        "approval_required": False}
            return {"job_id": "job_1"}

        client.create_job.side_effect = _create
        client.get_job.return_value = {"status": "SUCCEEDED", "exit_code": 0, "stdout": "ok", "stderr": ""}
        return client, p

    def test_write_returns_preview_without_dispatch(self):
        client, p = self._write_client()
        with p:
            r = m.exec_command("rm -rf /tmp/x", agent_id="agent_a")
        assert r["preview"] is True and r["confirmed"] is False and r["is_write"] is True
        # Only the dry-run classification ran; nothing was dispatched.
        assert client.create_job.call_count == 1
        assert client.create_job.call_args.kwargs.get("dry_run") is True

    def test_write_dispatches_with_confirm(self):
        client, p = self._write_client()
        with p:
            r = m.exec_command("rm -rf /tmp/x", agent_id="agent_a", confirm=True)
        assert r["status"] == "SUCCEEDED"
        client.create_job.assert_any_call("agent_a", "rm -rf /tmp/x")   # dispatched

    def test_read_runs_without_confirm(self):
        client, p = _mock_client(default_agent="agent_a")
        client.create_job.side_effect = lambda agent_id, command, dry_run=False: (
            {"dry_run": True, "is_write": False} if dry_run else {"job_id": "job_1"})
        client.get_job.return_value = {"status": "SUCCEEDED", "exit_code": 0, "stdout": "ok", "stderr": ""}
        with p:
            r = m.exec_command("uptime", agent_id="agent_a")
        assert r["status"] == "SUCCEEDED"

    def test_dry_run_error_surfaced(self):
        # A readonly-mode write is rejected 403 at the dry-run gate; surface it, don't dispatch.
        client, p = _mock_client(default_agent="agent_a")
        resp = MagicMock(status_code=403, content=b'{"error":"command not permitted in readonly mode"}')
        resp.json.return_value = {"error": "command not permitted in readonly mode"}
        client.create_job.side_effect = requests.HTTPError(response=resp)
        with p:
            r = m.exec_command("rm -rf /tmp/x", agent_id="agent_a")
        assert r["error"] == "command not permitted in readonly mode"


class TestGetJob:
    def test_redacts_output(self):
        client, p = _mock_client()
        client.get_job.return_value = {"status": "SUCCEEDED", "stdout": "Bearer abc123DEF", "stderr": None}
        with p:
            r = m.get_job("job_1")
        assert "abc123DEF" not in r["stdout"]


class TestListHistory:
    def test_caps_limit_at_100(self):
        client, p = _mock_client()
        client.list_jobs.return_value = {"jobs": []}
        with p:
            m.list_history(limit=500)
        assert client.list_jobs.call_args.kwargs["limit"] == 100

    def test_resolves_agent_alias(self):
        client, p = _mock_client()
        client.list_jobs.return_value = {"jobs": []}
        with p, patch.object(m.cfg_module, "resolve_agent", return_value="agent_real"):
            m.list_history(agent_id="prod", limit=5)
        assert client.list_jobs.call_args.kwargs["agent_id"] == "agent_real"


class TestApprovalTools:
    def test_list_approved_requires_agent(self):
        client, p = _mock_client(default_agent="")
        with p:
            r = m.list_approved_commands(agent_id="")
        assert "error" in r

    def test_list_approved_delegates(self):
        client, p = _mock_client(default_agent="agent_a")
        client.list_agent_approved.return_value = {"approved": []}
        with p:
            r = m.list_approved_commands()
        client.list_agent_approved.assert_called_once_with("agent_a")
        assert r == {"approved": []}

    def test_list_pending_requires_agent(self):
        client, p = _mock_client(default_agent="")
        with p:
            r = m.list_pending_approvals(agent_id="")
        assert "error" in r

    def test_list_pending_uses_pending_status(self):
        client, p = _mock_client(default_agent="agent_a")
        client.list_agent_approved.return_value = {"pending": []}
        with p:
            m.list_pending_approvals()
        client.list_agent_approved.assert_called_once_with("agent_a", status="pending")


class TestFleetExecConfirmGate:
    def test_default_is_dry_run_preview(self):
        client, p = _mock_client()
        client.list_fleets.return_value = {"fleets": [{"fleet_id": "fleet_1", "name": "web-asg"}]}
        # The preview is a server dry-run: the resolved plan, nothing dispatched.
        client.fleet_fanout.return_value = {
            "dry_run": True, "fleet_id": "fleet_1", "fleet_name": "web-asg",
            "command": "systemctl restart app", "mode": "approved", "matched": 1,
            "wave_size": 1, "wave_strategy": "manual", "failure_policy": "stop",
            "wave_total": 1, "is_write": True, "approval_required": True, "skipped": []}
        with p:
            r = m.fleet_exec("systemctl restart app", "web-asg")
        assert r["preview"] is True and r["confirmed"] is False
        assert r["matched"] == 1 and r["wave_strategy"] == "manual" and r["approval_required"] is True
        client.fleet_fanout.assert_called_once_with("fleet_1", "systemctl restart app", max_targets=None, dry_run=True)

    def test_confirm_true_returns_bounded_run_summary(self):
        client, p = _mock_client()
        client.list_fleets.return_value = {"fleets": [{"fleet_id": "fleet_1", "name": "web-asg"}]}
        client.fleet_fanout.return_value = {"run_id": "batch_a", "dispatched": 2, "skipped": []}
        # Bounded summary comes from run_status, not per-job stdout dumps.
        client.get_run.return_value = {"run_id": "batch_a", "state": "partial", "terminal": True,
                                       "counts": {"ok": 1, "failed": 1, "pending": 0, "running": 0},
                                       "failures": [{"agent_id": "agent_m2", "exit_code": 1, "stderr": "boom"}]}
        with p:
            r = m.fleet_exec("uptime", "web-asg", confirm=True)
        assert r["confirmed"] is True and r["run_id"] == "batch_a"
        assert r["state"] == "partial" and r["counts"]["failed"] == 1
        assert r["failures"][0]["agent_id"] == "agent_m2"
        assert "results" not in r   # no per-member stdout dump
        client.fleet_fanout.assert_called_once_with("fleet_1", "uptime", max_targets=None, idempotency_key=None)

    def test_cap_exceeded_returns_hint(self):
        import requests
        client, p = _mock_client()
        client.list_fleets.return_value = {"fleets": [{"fleet_id": "fleet_1", "name": "web-asg"}]}
        resp = MagicMock(status_code=409, content=b"{}")
        resp.json.return_value = {"error": "30 targets exceeds the fan-out safety cap of 25."}
        client.fleet_fanout.side_effect = requests.HTTPError(response=resp)
        with p:
            r = m.fleet_exec("uptime", "web-asg", confirm=True)
        assert "cap" in r["error"] and "max_targets" in r["hint"]

    def test_run_status_tool(self):
        client, p = _mock_client()
        client.get_run.return_value = {"run_id": "batch_a", "state": "succeeded", "terminal": True,
                                       "counts": {"ok": 3, "failed": 0, "pending": 0, "running": 0}, "failures": []}
        with p:
            r = m.run_status("batch_a")
        assert r["state"] == "succeeded" and r["counts"]["ok"] == 3

    def test_server_staged_run_reports_wave_info(self):
        # Staging is policy-driven server-side; a staged dispatch response surfaces waves.
        client, p = _mock_client()
        client.list_fleets.return_value = {"fleets": [{"fleet_id": "fleet_1", "name": "web-asg"}]}
        client.fleet_fanout.return_value = {"run_id": "run_s", "dispatched": 2, "wave_total": 3,
                                            "skipped": []}
        client.get_run.return_value = {"run_id": "run_s", "state": "running", "terminal": False,
                                       "counts": {"ok": 0, "failed": 0, "pending": 2, "running": 0},
                                       "current_wave": 0, "staged": 3, "failures": []}
        with p:
            r = m.fleet_exec("deploy.sh", "web-asg", confirm=True, timeout=0)
        client.fleet_fanout.assert_called_once_with(
            "fleet_1", "deploy.sh", max_targets=None, idempotency_key=None)
        assert r["staged"] is True and r["wave_total"] == 3


class TestRunControlTools:
    def test_run_pause(self):
        client, p = _mock_client()
        client.pause_run.return_value = {"run_id": "run_s", "state": "paused"}
        with p:
            r = m.run_pause("run_s")
        assert r["state"] == "paused"
        client.pause_run.assert_called_once_with("run_s")

    def test_run_resume(self):
        client, p = _mock_client()
        client.resume_run.return_value = {"run_id": "run_s", "state": "running"}
        with p:
            r = m.run_resume("run_s")
        assert r["state"] == "running"

    def test_run_cancel(self):
        client, p = _mock_client()
        client.cancel_run.return_value = {"run_id": "run_s", "state": "canceled", "canceled": 3}
        with p:
            r = m.run_cancel("run_s")
        assert r["state"] == "canceled"

    def test_run_pause_404(self):
        import requests
        client, p = _mock_client()
        resp = requests.Response(); resp.status_code = 404
        client.pause_run.side_effect = requests.HTTPError(response=resp)
        with p:
            r = m.run_pause("nope")
        assert "error" in r

    def test_unknown_fleet_errors(self):
        client, p = _mock_client()
        client.list_fleets.return_value = {"fleets": []}
        with p:
            r = m.fleet_exec("uptime", "nope", confirm=True)
        assert "error" in r
        client.fleet_fanout.assert_not_called()


class TestNoApprovalWriteTools:
    def test_ai_cannot_create_approve_or_deny(self):
        # Approvals are a human review control: the MCP surface exposes no tool to
        # create, approve, or deny them (only read-only visibility). This prevents an
        # AI from requesting a command and then approving it itself.
        assert not hasattr(m, "request_approval")
        assert not hasattr(m, "approve_approval")
        assert not hasattr(m, "deny_approval")


class TestExecByTagConfirmGate:
    def test_preview_by_default_shows_wave_plan(self):
        client, p = _mock_client()
        client.fanout_by_tag.return_value = {
            "dry_run": True, "tag": "env:prod", "type": "host", "command": "uptime",
            "matched": 3, "wave_size": 3, "wave_strategy": "auto", "failure_policy": "continue",
            "wave_total": 1, "skipped": []}
        with p:
            r = m.exec_by_tag("uptime", "env:prod")
        assert r["preview"] is True and r["confirmed"] is False
        assert r["wave_size"] == 3 and r["wave_strategy"] == "auto"
        client.fanout_by_tag.assert_called_once_with("env:prod", "uptime", agent_type=None, dry_run=True)
        client.create_job.assert_not_called()

    def test_confirm_dispatches_via_fanout(self):
        client, p = _mock_client()
        client.fanout_by_tag.return_value = {"tag": "env:prod", "type": "host", "run_id": "run_t",
                                             "dispatched": 2, "skipped": [], "wave_total": 1}
        client.get_run.return_value = {"run_id": "run_t", "state": "succeeded", "terminal": True,
                                       "counts": {"ok": 2, "failed": 0, "pending": 0, "running": 0}, "failures": []}
        with p:
            r = m.exec_by_tag("uptime", "env:prod", confirm=True)
        assert r["confirmed"] is True and r["dispatched"] == 2 and r["run_id"] == "run_t"
        client.fanout_by_tag.assert_any_call("env:prod", "uptime", agent_type=None, dry_run=False)

    def test_ambiguous_type_surfaces_error(self):
        import requests
        client, p = _mock_client()
        resp = requests.Response(); resp.status_code = 409
        resp._content = b'{"error": "tag matches both host and k8s agents; pass type=host or type=k8s"}'
        client.fanout_by_tag.side_effect = requests.HTTPError(response=resp)
        with p:
            r = m.exec_by_tag("uptime", "env:prod")
        assert "both host and k8s" in r["error"]


class TestTagRuns:
    def test_list_tag_runs_delegates(self):
        client, p = _mock_client()
        client.list_tag_runs.return_value = {"runs": [{"run_id": "batch_t", "tag": "env:prod"}]}
        with p:
            r = m.list_tag_runs()
        assert r["runs"][0]["tag"] == "env:prod"
        client.list_tag_runs.assert_called_once_with(limit=20)

    def test_list_tag_runs_caps_limit(self):
        client, p = _mock_client()
        client.list_tag_runs.return_value = {"runs": []}
        with p:
            m.list_tag_runs(limit=500)
        client.list_tag_runs.assert_called_once_with(limit=100)

    def test_list_tag_run_expands_batch(self):
        client, p = _mock_client()
        client.list_jobs.return_value = {"jobs": [{"job_id": "j1", "run_tag": "env:prod"}]}
        with p:
            r = m.list_tag_run("batch_t")
        assert r["jobs"][0]["job_id"] == "j1"
        client.list_jobs.assert_called_once_with(run_id="batch_t", limit=100)
