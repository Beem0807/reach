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
        client.create_job.assert_called_once_with("agent_a", "env")

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
