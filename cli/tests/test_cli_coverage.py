"""
Additional coverage tests for paths not covered by test_cli.py.
Organised by module section; each test targets specific missing lines.
"""
import json
import sys
import time
import types
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Stub the mcp SDK so mcp_server can be imported without the real dependency.
if "mcp" not in sys.modules:
    class _FakeFastMCP:
        def __init__(self, *args, **kwargs): pass
        def tool(self, *a, **kw):
            def deco(fn): return fn
            return deco
        def run(self): pass

    _fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    _fastmcp_mod.FastMCP = _FakeFastMCP
    _server_mod = types.ModuleType("mcp.server")
    _server_mod.fastmcp = _fastmcp_mod
    _root = types.ModuleType("mcp")
    _root.server = _server_mod
    sys.modules["mcp"] = _root
    sys.modules["mcp.server"] = _server_mod
    sys.modules["mcp.server.fastmcp"] = _fastmcp_mod

from typer.testing import CliRunner
from reach.main import app

try:
    runner = CliRunner(mix_stderr=False)
except TypeError:
    runner = CliRunner()

_PROFILE = {
    "api_url": "https://api.example.com",
    "api_key": "tok_test",
    "default_agent_id": "agent_a",
}

_AGENT = {
    "agent_id": "agent_a",
    "status": "ACTIVE",
    "hostname": "prod-1",
    "agent_version": "0.1.0",
    "machine_fingerprint": "fp_abc",
    "claimed_at": "2026-06-17T10:00:00+00:00",
    "token_issued_at": "2026-06-17T10:00:00+00:00",
    "last_heartbeat_at": "2026-06-17T10:01:00+00:00",
    "active_until": None,
    "mode": "wild",
    "access_level": "open",
    "tags": [],
}

_JOB_PENDING = {
    "job_id": "job_1", "agent_id": "agent_a", "created_by": "user_1",
    "command": "ls", "status": "PENDING", "exit_code": None,
    "stdout": None, "stderr": None, "duration_ms": None,
    "created_at": "2026-06-17T10:00:00+00:00",
    "started_at": None, "completed_at": None,
}

_JOB_SUCCEEDED = {**_JOB_PENDING, "status": "SUCCEEDED", "exit_code": 0,
                  "stdout": "file1\nfile2\n", "stderr": "", "duration_ms": 42}


def _mock_cfg(profile=_PROFILE, aliases=None):
    aliases = aliases or {}

    def _require(key):
        val = profile.get(key)
        if not val:
            raise SystemExit(f"missing {key}")
        return val

    return patch.multiple(
        "reach.main.cfg_module",
        require=MagicMock(side_effect=_require),
        load_profile=MagicMock(return_value=profile),
        save_profile=MagicMock(),
        active_profile_name=MagicMock(return_value="default"),
        load=MagicMock(return_value={"active_profile": "default", "profiles": {"default": profile}}),
        save=MagicMock(),
        list_aliases=MagicMock(return_value=aliases),
        resolve_agent=MagicMock(side_effect=lambda x: aliases.get(x, x)),
        set_alias=MagicMock(),
        remove_alias=MagicMock(return_value=True),
        set_active_profile=MagicMock(),
        delete_profile=MagicMock(),
        rename_profile=MagicMock(),
        list_profiles=MagicMock(return_value=["default"]),
    )


# ---------------------------------------------------------------------------
# reach login - overwrite cancel (line 90)
# ---------------------------------------------------------------------------

class TestLoginOverwriteCancel:
    def test_existing_profile_overwrite_cancelled_exits_0(self):
        existing = {"api_url": "https://old.example.com", "api_key": "tok_old"}
        with patch("reach.main.cfg_module.load",
                   return_value={"active_profile": "default", "profiles": {"default": existing}}), \
             patch("reach.main.cfg_module.save") as mock_save:
            result = runner.invoke(
                app, ["login", "--api-url", "https://new.com", "--api-key", "tok_new"],
                input="n\n",
            )
        assert result.exit_code == 0
        mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# reach whoami - optional fields printed (lines 124, 126)
# ---------------------------------------------------------------------------

class TestWhoamiOptionalFields:
    def test_prints_username_when_present(self):
        me = {"user_id": "u1", "tenant_id": "t1", "name": "Alice",
              "username": "alice", "role": None, "created_at": "2026-01-01"}
        mock_client = MagicMock()
        mock_client.get_me.return_value = me
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["whoami"])
        assert "alice" in result.output

    def test_prints_role_when_present(self):
        me = {"user_id": "u1", "tenant_id": "t1", "name": "Alice",
              "username": None, "role": "admin", "created_at": "2026-01-01"}
        mock_client = MagicMock()
        mock_client.get_me.return_value = me
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["whoami"])
        assert "admin" in result.output


# ---------------------------------------------------------------------------
# reach agents list - HTTP error (lines 182-184)
# ---------------------------------------------------------------------------

class TestAgentsListHttpError:
    def test_http_error_exits_2(self):
        import requests
        mock_resp = MagicMock(status_code=500, text="internal error")
        mock_client = MagicMock()
        mock_client.list_agents.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["agents", "list"])
        assert result.exit_code == 2
        assert "500" in result.output


# ---------------------------------------------------------------------------
# reach exec - timeout (lines 340-342) and poll HTTP error (346-348)
# ---------------------------------------------------------------------------

class TestExecTimeout:
    def test_timeout_exits_2_with_message(self):
        mock_client = MagicMock()
        mock_client.create_job.return_value = {"job_id": "job_1"}
        # get_job always returns PENDING so we hit the timeout
        mock_client.get_job.return_value = _JOB_PENDING
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client), \
             patch("reach.main.time.monotonic", side_effect=[0, 0, 999]):
            result = runner.invoke(app, ["exec", "--timeout", "1", "--", "ls"])
        assert result.exit_code == 2
        assert "timed out" in result.output.lower()

    def test_poll_http_error_exits_2(self):
        import requests
        mock_resp = MagicMock(status_code=500, text="poll error")
        mock_client = MagicMock()
        mock_client.create_job.return_value = {"job_id": "job_1"}
        mock_client.get_job.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client), \
             patch("reach.main.time.monotonic", return_value=0):
            result = runner.invoke(app, ["exec", "--timeout", "60", "--", "ls"])
        assert result.exit_code == 2
        assert "poll" in result.output.lower() or "Error" in result.output


# ---------------------------------------------------------------------------
# reach history - HTTP error (lines 401-403)
# ---------------------------------------------------------------------------

class TestHistoryHttpError:
    def test_http_error_exits_2(self):
        import requests
        mock_resp = MagicMock(status_code=403, text="forbidden")
        mock_client = MagicMock()
        mock_client.list_jobs.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["jobs"])
        assert result.exit_code == 2
        assert "403" in result.output


# ---------------------------------------------------------------------------
# _expires_label - invalid date ValueError (lines 472-473)
# ---------------------------------------------------------------------------

class TestExpiresLabelInvalidDate:
    def test_invalid_date_returns_raw_truncated(self):
        from reach.main import _expires_label
        label = _expires_label({"expires_at": "not-a-valid-date-string"})
        assert "not-a-valid" in label or "dim" in label


# ---------------------------------------------------------------------------
# reach agent-init - interactive choice (lines 558-564)
# ---------------------------------------------------------------------------

class TestAgentInitInteractive:
    def test_interactive_choice_1_selects_claude(self, tmp_path):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": []}
        claude_md = tmp_path / "CLAUDE.md"
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client), \
             patch("reach.main.Prompt.ask", side_effect=["1", "", "", "", ""]), \
             patch("reach.main.Path", side_effect=lambda p: tmp_path / p if p == "CLAUDE.md" else Path(p)):
            result = runner.invoke(app, ["agent-init"])
        assert result.exit_code == 0

    def test_interactive_choice_4_selects_mcp(self):
        with patch("reach.main.Prompt.ask", return_value="4"):
            result = runner.invoke(app, ["agent-init"])
        assert result.exit_code == 0
        assert "mcpServers" in result.output


# ---------------------------------------------------------------------------
# reach agent-init -for cursor (line 632)
# reach agent-init - manual fallback when API is unavailable (619-625)
# ---------------------------------------------------------------------------

class TestAgentInitCursor:
    def test_for_cursor_creates_mdc_file(self, tmp_path):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": [_AGENT]}
        rules_dir = tmp_path / ".cursor" / "rules"

        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client), \
             patch("reach.main.Prompt.ask", return_value=""), \
             patch("reach.main.Path", side_effect=lambda p: (
                 tmp_path / ".cursor" / "rules" if p == ".cursor/rules" else Path(p)
             )):
            result = runner.invoke(app, ["agent-init", "--for", "cursor"])
        assert result.exit_code == 0


class TestAgentInitManualFallback:
    def test_no_api_connection_prompts_manually(self, tmp_path):
        mock_client = MagicMock()
        mock_client.list_agents.side_effect = Exception("network error")
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client), \
             patch("reach.main.Prompt.ask", return_value=""), \
             patch("reach.main.Path", side_effect=lambda p: tmp_path / p if p == "CLAUDE.md" else Path(p)):
            result = runner.invoke(app, ["agent-init", "--for", "claude"])
        assert result.exit_code == 0
        assert "manually" in result.output.lower() or "Could not" in result.output


# ---------------------------------------------------------------------------
# _write_claude_md (lines 659-672)
# ---------------------------------------------------------------------------

class TestWriteClaudeMd:
    def test_creates_new_claude_md(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from reach.main import _write_claude_md
        _write_claude_md("## Remote Access\n\nsome content")
        assert (tmp_path / "CLAUDE.md").exists()
        assert "Remote Access" in (tmp_path / "CLAUDE.md").read_text()

    def test_appends_to_existing_file_without_remote_access(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("# Existing content\n")
        from reach.main import _write_claude_md
        _write_claude_md("## Remote Access\n\nnew section")
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "Existing content" in content
        assert "Remote Access" in content

    def test_replaces_existing_remote_access_section(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("# Preamble\n\n## Remote Access\n\nold content")
        from reach.main import _write_claude_md
        with patch("reach.main.Confirm.ask", return_value=True):
            _write_claude_md("## Remote Access\n\nnew content")
        content = (tmp_path / "CLAUDE.md").read_text()
        assert "new content" in content
        assert "old content" not in content
        assert "Preamble" in content

    def test_cancel_overwrite_exits_0(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "CLAUDE.md").write_text("## Remote Access\n\nold")
        from reach.main import _write_claude_md
        import typer
        with patch("reach.main.Confirm.ask", return_value=False):
            with pytest.raises(typer.Exit):
                _write_claude_md("## Remote Access\n\nnew")


# ---------------------------------------------------------------------------
# _write_cursor_rules (lines 676-682)
# ---------------------------------------------------------------------------

class TestWriteCursorRules:
    def test_creates_mdc_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from reach.main import _write_cursor_rules
        _write_cursor_rules("## Remote Access\n\nsome content")
        mdc = tmp_path / ".cursor" / "rules" / "reach.mdc"
        assert mdc.exists()
        assert "Remote Access" in mdc.read_text()
        assert "alwaysApply: true" in mdc.read_text()


# ---------------------------------------------------------------------------
# _print_job_result - trailing newline edge cases (lines 820, 826)
# ---------------------------------------------------------------------------

class TestPrintJobResultTrailingNewline:
    def test_stdout_without_trailing_newline_gets_extra_newline(self):
        from reach.main import _print_job_result, console
        job = {**_JOB_SUCCEEDED, "stdout": "no newline at end", "stderr": ""}
        calls = []
        with patch.object(console, "print", side_effect=lambda *a, **kw: calls.append((a, kw))):
            _print_job_result(job)
        # Last console.print for stdout block should have been called without end=""
        stdout_calls = [c for c in calls if c[0] and "no newline" in str(c[0][0])]
        assert len(stdout_calls) > 0

    def test_stderr_without_trailing_newline_gets_extra_newline(self):
        from reach.main import _print_job_result, console
        job = {**_JOB_SUCCEEDED, "stderr": "err no newline", "stdout": ""}
        calls = []
        with patch.object(console, "print", side_effect=lambda *a, **kw: calls.append((a, kw))):
            _print_job_result(job)
        stderr_calls = [c for c in calls if c[0] and "err no newline" in str(c[0][0])]
        assert len(stderr_calls) > 0


# ---------------------------------------------------------------------------
# reach profile delete (lines 873-883)
# ---------------------------------------------------------------------------

class TestProfileDelete:
    def test_delete_unknown_profile_exits_2(self):
        full = {"active_profile": "default", "profiles": {"default": {}}}
        with patch("reach.main.cfg_module.load", return_value=full):
            result = runner.invoke(app, ["profile", "delete", "nonexistent"])
        assert result.exit_code == 2
        assert "not found" in result.output

    def test_delete_active_profile_exits_2(self):
        full = {"active_profile": "default", "profiles": {"default": {}}}
        with patch("reach.main.cfg_module.load", return_value=full):
            result = runner.invoke(app, ["profile", "delete", "default"])
        assert result.exit_code == 2
        assert "active" in result.output

    def test_delete_cancelled_exits_0(self):
        full = {"active_profile": "default", "profiles": {"default": {}, "old": {}}}
        with patch("reach.main.cfg_module.load", return_value=full), \
             patch("reach.main.cfg_module.delete_profile"):
            result = runner.invoke(app, ["profile", "delete", "old"], input="n\n")
        assert result.exit_code == 0

    def test_delete_confirmed_calls_delete_profile(self):
        full = {"active_profile": "default", "profiles": {"default": {}, "old": {}}}
        with patch("reach.main.cfg_module.load", return_value=full), \
             patch("reach.main.cfg_module.delete_profile") as mock_del:
            result = runner.invoke(app, ["profile", "delete", "old"], input="y\n")
        mock_del.assert_called_once_with("old")
        assert "Deleted" in result.output


# ---------------------------------------------------------------------------
# reach mcp (lines 906-907)
# ---------------------------------------------------------------------------

class TestMcpCommand:
    def test_mcp_delegates_to_mcp_main(self):
        with patch("reach.mcp_server.main") as mock_main:
            result = runner.invoke(app, ["mcp"])
        mock_main.assert_called_once()


# ---------------------------------------------------------------------------
# reach man (lines 916-985)
# ---------------------------------------------------------------------------

class TestManCommand:
    def test_man_exits_0(self):
        result = runner.invoke(app, ["man"])
        assert result.exit_code == 0

    def test_man_includes_exec_section(self):
        result = runner.invoke(app, ["man"])
        assert "exec" in result.output.lower()

    def test_man_includes_auth_section(self):
        result = runner.invoke(app, ["man"])
        assert "login" in result.output.lower()

    def test_man_includes_agent_section(self):
        result = runner.invoke(app, ["man"])
        assert "agents" in result.output.lower()


# ---------------------------------------------------------------------------
# mcp_server.main() (lines 271, 275)
# ---------------------------------------------------------------------------

class TestMcpServerMain:
    def test_main_calls_mcp_run(self):
        import reach.mcp_server as mcp_server
        with patch.object(mcp_server.mcp, "run") as mock_run:
            mcp_server.main()
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# config.py - require() api_key backward compat (line 82)
# ---------------------------------------------------------------------------

class TestRequireApiKeyBackwardCompat:
    def test_falls_back_to_tenant_token_for_api_key(self, tmp_path, monkeypatch):
        import reach.config as cfg
        monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
        (tmp_path / "config.json").write_text(json.dumps({
            "active_profile": "default",
            "profiles": {"default": {"api_url": "https://x.com", "tenant_token": "legacy_tok"}}
        }))
        assert cfg.require("api_key") == "legacy_tok"
