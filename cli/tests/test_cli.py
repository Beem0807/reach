"""
Tests for reach/main.py CLI commands.
Strategy:
  - CliRunner invokes commands without spawning a subprocess.
  - cfg_module functions are patched so tests never touch ~/.reach/config.json.
  - ReachClient is patched so tests never make real HTTP calls.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

from reach.main import app

# Click >= 8.2 dropped the mix_stderr kwarg (stdout/stderr are always separate);
# older Click needs it to keep stderr out of result.output.
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
    "machine_fingerprint": "fp_abc123",
    "claimed_at": "2026-06-17T10:00:00+00:00",
    "token_issued_at": "2026-06-17T10:00:00+00:00",
    "last_heartbeat_at": "2026-06-17T10:01:00+00:00",
    "active_until": "2026-06-17T11:01:00+00:00",
    "mode": "wild",
    "access_level": "open",
    "tags": [],
}

_JOB = {
    "job_id": "job_1",
    "agent_id": "agent_a",
    "created_by": "user_1",
    "command": "ls",
    "status": "SUCCEEDED",
    "exit_code": 0,
    "stdout": "file1\nfile2\n",
    "stderr": "",
    "duration_ms": 42,
    "created_at": "2026-06-17T10:00:00+00:00",
    "started_at": "2026-06-17T10:00:01+00:00",
    "completed_at": "2026-06-17T10:00:01+00:00",
}

_ME = {
    "user_id": "user_1",
    "tenant_id": "tenant_1",
    "name": "Alice",
    "created_at": "2026-06-17T10:00:00+00:00",
}


def _mock_cfg(profile=_PROFILE, profile_name="default", aliases=None):
    """Return a context manager that patches all cfg_module calls."""
    aliases = aliases or {}

    def _require(key):
        val = profile.get(key)
        if not val:
            raise SystemExit(f"missing {key}")
        return val

    patcher = patch.multiple(
        "reach.main.cfg_module",
        require=MagicMock(side_effect=_require),
        load_profile=MagicMock(return_value=profile),
        save_profile=MagicMock(),
        active_profile_name=MagicMock(return_value=profile_name),
        load=MagicMock(return_value={"active_profile": profile_name, "profiles": {profile_name: profile}}),
        save=MagicMock(),
        list_aliases=MagicMock(return_value=aliases),
        resolve_agent=MagicMock(side_effect=lambda x: aliases.get(x, x)),
        set_alias=MagicMock(),
        remove_alias=MagicMock(return_value=True),
        set_active_profile=MagicMock(),
        delete_profile=MagicMock(),
        rename_profile=MagicMock(),
        list_profiles=MagicMock(return_value=[profile_name]),
    )
    return patcher


# ---------------------------------------------------------------------------
# reach --version
# ---------------------------------------------------------------------------

class TestVersion:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "reach" in result.output

    def test_version_flag_short(self):
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert "reach" in result.output

    def test_no_version_subcommand(self):
        # `reach version` is intentionally not a subcommand - only the flag exists.
        result = runner.invoke(app, ["version"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# reach config show
# ---------------------------------------------------------------------------

class TestConfigShow:
    def test_shows_profile_details(self):
        with _mock_cfg():
            result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "https://api.example.com" in result.output
        assert "agent_a" in result.output

    def test_exits_2_when_no_config(self):
        with patch.multiple("reach.main.cfg_module",
                            active_profile_name=MagicMock(return_value="default"),
                            load_profile=MagicMock(return_value={})):
            result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 2

    def test_shows_aliases_when_present(self):
        profile_with_aliases = {**_PROFILE, "aliases": {"prod": "agent_a"}}
        with _mock_cfg(profile=profile_with_aliases):
            result = runner.invoke(app, ["config", "show"])
        assert "prod" in result.output


# ---------------------------------------------------------------------------
# reach login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_saves_new_profile(self):
        with patch("reach.main.cfg_module.load", return_value={"active_profile": "default", "profiles": {}}), \
             patch("reach.main.cfg_module.save") as mock_save:
            result = runner.invoke(app, ["login", "--api-url", "https://api.example.com", "--api-key", "tok_abc"])
        assert result.exit_code == 0
        assert "Logged in" in result.output
        mock_save.assert_called_once()

    def test_strips_trailing_slash_from_url(self):
        saved = {}
        def _save(data):
            saved.update(data)
        with patch("reach.main.cfg_module.load", return_value={"active_profile": "default", "profiles": {}}), \
             patch("reach.main.cfg_module.save", side_effect=_save):
            runner.invoke(app, ["login", "--api-url", "https://api.example.com/", "--api-key", "tok"])
        assert saved["profiles"]["default"]["api_url"] == "https://api.example.com"

    def test_saves_api_key_not_tenant_token(self):
        saved = {}
        def _save(data):
            saved.update(data)
        with patch("reach.main.cfg_module.load", return_value={"active_profile": "default", "profiles": {}}), \
             patch("reach.main.cfg_module.save", side_effect=_save):
            runner.invoke(app, ["login", "--api-url", "https://api.example.com", "--api-key", "tok_abc"])
        profile = saved["profiles"]["default"]
        assert profile.get("api_key") == "tok_abc"
        assert "tenant_token" not in profile

    def test_login_removes_legacy_tenant_token(self):
        saved = {}
        def _save(data):
            saved.update(data)
        existing = {"api_url": "https://old.example.com", "tenant_token": "old_tok"}
        with patch("reach.main.cfg_module.load", return_value={"active_profile": "default", "profiles": {"default": existing}}), \
             patch("reach.main.cfg_module.save", side_effect=_save):
            runner.invoke(app, ["login", "--api-url", "https://api.example.com", "--api-key", "new_tok"], input="y\n")
        profile = saved["profiles"]["default"]
        assert "tenant_token" not in profile
        assert profile.get("api_key") == "new_tok"

    def test_custom_profile_name(self):
        saved = {}
        def _save(data):
            saved.update(data)
        with patch("reach.main.cfg_module.load", return_value={"active_profile": "default", "profiles": {}}), \
             patch("reach.main.cfg_module.save", side_effect=_save):
            runner.invoke(app, ["login", "--api-url", "https://api.example.com", "--api-key", "tok", "--profile", "prod"])
        assert "prod" in saved.get("profiles", {})


# ---------------------------------------------------------------------------
# reach whoami
# ---------------------------------------------------------------------------

class TestWhoami:
    def test_prints_user_info(self):
        mock_client = MagicMock()
        mock_client.get_me.return_value = _ME
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 0
        assert "user_1" in result.output
        assert "tenant_1" in result.output
        assert "Alice" in result.output

    def test_http_error_exits_2(self):
        import requests
        mock_resp = MagicMock(status_code=401, text="unauthorized")
        mock_client = MagicMock()
        mock_client.get_me.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 2
        assert "401" in result.output


# ---------------------------------------------------------------------------
# reach status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_shows_agent_table(self):
        mock_client = MagicMock()
        mock_client.get_agent.return_value = _AGENT
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "agent_a" in result.output
        assert "prod-1" in result.output

    def test_http_error_exits_2(self):
        import requests
        mock_resp = MagicMock(status_code=404, text="not found")
        mock_client = MagicMock()
        mock_client.get_agent.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# reach agents list
# ---------------------------------------------------------------------------

class TestAgentsList:
    def test_shows_agents_table(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": [_AGENT]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["agents", "list"])
        assert result.exit_code == 0
        assert "agent_a" in result.output
        assert "prod-1" in result.output

    def test_no_agents_prints_message(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["agents", "list"])
        assert "No standalone agents" in result.output

    def test_tag_filter_passed_to_client(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            runner.invoke(app, ["agents", "list", "--tag", "env:prod"])
        mock_client.list_agents.assert_called_once_with(tag="env:prod")

    def test_tags_column_shown_when_agent_has_tags(self):
        agent_with_tags = {**_AGENT, "tags": ["env:prod"]}
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": [agent_with_tags]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            # Render at a normal terminal width so the full table (incl. the Tags
            # column) is not truncated to the 80-col non-tty default.
            result = runner.invoke(app, ["agents", "list"], env={"COLUMNS": "160"})
        assert "env:prod" in result.output

    def test_default_agent_marked(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": [_AGENT]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["agents", "list"])
        assert "default" in result.output

    def test_alias_shown_in_table(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": [_AGENT]}
        with _mock_cfg(aliases={"myprod": "agent_a"}), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["agents", "list"])
        assert "myprod" in result.output


# ---------------------------------------------------------------------------
# reach agents use
# ---------------------------------------------------------------------------

class TestAgentsUse:
    def test_sets_default_agent(self):
        with _mock_cfg() as mocks:
            result = runner.invoke(app, ["agents", "use", "agent_b"])
        assert result.exit_code == 0
        assert "agent_b" in result.output

    def test_resolves_alias_before_saving(self):
        saved = {}
        with _mock_cfg(aliases={"prod": "agent_b"}) as mocks:
            with patch("reach.main.cfg_module.save_profile", side_effect=lambda d: saved.update(d)):
                runner.invoke(app, ["agents", "use", "prod"])
        assert saved.get("default_agent_id") == "agent_b"


# ---------------------------------------------------------------------------
# reach alias
# ---------------------------------------------------------------------------

class TestAlias:
    def test_set_alias(self):
        with _mock_cfg():
            result = runner.invoke(app, ["alias", "set", "prod", "agent_a"])
        assert result.exit_code == 0
        assert "prod" in result.output

    def test_remove_existing_alias(self):
        with _mock_cfg():
            result = runner.invoke(app, ["alias", "remove", "prod"])
        assert result.exit_code == 0

    def test_remove_missing_alias_exits_2(self):
        with patch.multiple("reach.main.cfg_module",
                            remove_alias=MagicMock(return_value=False),
                            resolve_agent=MagicMock(side_effect=lambda x: x)):
            result = runner.invoke(app, ["alias", "remove", "nonexistent"])
        assert result.exit_code == 2

    def test_add_is_alias_for_set(self):
        with _mock_cfg():
            result = runner.invoke(app, ["alias", "add", "prod", "agent_a"])
        assert result.exit_code == 0
        assert "Alias set" in result.output and "prod" in result.output

    def test_rm_is_alias_for_remove(self):
        with _mock_cfg():
            result = runner.invoke(app, ["alias", "rm", "prod"])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()

    def test_verb_aliases_hidden_from_help(self):
        result = runner.invoke(app, ["alias", "--help"])
        assert "set" in result.output and "remove" in result.output
        # add/rm work but shouldn't clutter the command list
        assert " add " not in result.output and " rm " not in result.output

    def test_list_aliases(self):
        with _mock_cfg(aliases={"prod": "agent_a", "staging": "agent_b"}):
            result = runner.invoke(app, ["alias", "list"])
        assert "prod" in result.output
        assert "staging" in result.output

    def test_list_aliases_empty(self):
        with patch.multiple("reach.main.cfg_module",
                            list_aliases=MagicMock(return_value={})):
            result = runner.invoke(app, ["alias", "list"])
        assert "No aliases" in result.output


# ---------------------------------------------------------------------------
# reach exec
# ---------------------------------------------------------------------------

class TestExec:
    def _client(self, job_status="SUCCEEDED", exit_code=0):
        mock_client = MagicMock()
        mock_client.create_job.return_value = {"job_id": "job_1"}
        mock_client.get_job.return_value = {**_JOB, "status": job_status, "exit_code": exit_code}
        return mock_client

    def test_no_command_exits_2(self):
        # Usage errors exit 2 (reach-level), distinct from a remote command failing (1).
        with _mock_cfg():
            result = runner.invoke(app, ["exec"])
        assert result.exit_code == 2

    def test_successful_exec(self):
        mock_client = self._client()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--", "ls"])
        assert result.exit_code == 0
        assert "job_1" in result.output

    def test_no_wait_exits_without_polling(self):
        mock_client = self._client()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--no-wait", "--", "ls"])
        assert result.exit_code == 0
        mock_client.get_job.assert_not_called()

    def test_failed_job_exits_with_exit_code(self):
        mock_client = self._client(job_status="FAILED", exit_code=1)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--", "ls"])
        assert result.exit_code == 1

    def test_rejected_job_exits_nonzero(self):
        mock_client = self._client(job_status="REJECTED", exit_code=None)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--", "ls"])
        assert result.exit_code != 0

    def test_expired_job_exits_nonzero(self):
        mock_client = self._client(job_status="EXPIRED", exit_code=None)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--", "ls"])
        assert result.exit_code != 0

    def test_target_agent_override(self):
        mock_client = self._client()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            runner.invoke(app, ["exec", "--agent", "agent_b", "--", "ls"])
        # A read runs straight through (dry-run pre-check reports is_write False) and
        # dispatches to the chosen agent.
        mock_client.create_job.assert_any_call("agent_b", "ls")

    def test_multi_word_command_joined(self):
        mock_client = self._client()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            runner.invoke(app, ["exec", "--", "docker", "ps", "-a"])
        # Last call is the real dispatch (after the read dry-run pre-check).
        _, command = mock_client.create_job.call_args[0]
        assert command == "docker ps -a"

    def _write_client(self):
        # dry_run pre-check reports a write; the real dispatch returns a job.
        mc = MagicMock()

        def _create(agent_id, command="", dry_run=False, argv=None):
            if dry_run:
                return {"dry_run": True, "is_write": True, "hostname": "web-01",
                        "mode": "wild", "agent_id": agent_id, "command": command}
            return {"job_id": "job_1"}

        mc.create_job.side_effect = _create
        mc.get_job.return_value = {**_JOB, "status": "SUCCEEDED", "exit_code": 0}
        return mc

    def test_write_command_prompts_and_dispatches_on_yes(self):
        mc = self._write_client()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mc):
            result = runner.invoke(app, ["exec", "--agent", "agent_a", "--", "rm", "-rf", "/tmp/x"], input="y\n")
        assert result.exit_code == 0
        assert "Proceed?" in result.output
        mc.create_job.assert_any_call("agent_a", "rm -rf /tmp/x")   # dispatched

    def test_write_command_aborts_on_decline(self):
        mc = self._write_client()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mc):
            result = runner.invoke(app, ["exec", "--agent", "agent_a", "--", "rm", "-rf", "/tmp/x"], input="n\n")
        assert result.exit_code == 2
        assert "Aborted" in result.output
        # Only the dry-run pre-check ran; nothing was dispatched.
        assert mc.create_job.call_count == 1
        assert mc.create_job.call_args.kwargs.get("dry_run") is True

    def test_write_command_force_skips_prompt(self):
        mc = self._write_client()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mc):
            result = runner.invoke(app, ["exec", "-y", "--agent", "agent_a", "--no-wait", "--", "rm", "-rf", "/tmp/x"])
        assert result.exit_code == 0
        assert "Proceed?" not in result.output
        # --force skips the pre-check entirely: a single dispatch call, no dry_run.
        mc.create_job.assert_called_once_with("agent_a", "rm -rf /tmp/x")

    def test_read_command_does_not_prompt(self):
        mc = self._write_client()
        mc.create_job.side_effect = lambda agent_id, command="", dry_run=False, argv=None: (
            {"dry_run": True, "is_write": False} if dry_run else {"job_id": "job_1"})
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mc):
            result = runner.invoke(app, ["exec", "--agent", "agent_a", "--", "uptime"])
        assert result.exit_code == 0
        assert "Proceed?" not in result.output

    def test_http_error_on_create_exits_2(self):
        import requests
        mock_client = MagicMock()
        mock_resp = MagicMock(status_code=403, text="forbidden")
        mock_client.create_job.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--", "ls"])
        assert result.exit_code == 2  # API error = reach-level failure

    def test_stdout_printed_on_success(self):
        mock_client = self._client()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--", "ls"])
        assert "file1" in result.output

    def test_double_dash_stripped(self):
        mock_client = self._client()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            runner.invoke(app, ["exec", "--", "ls"])
        _, command = mock_client.create_job.call_args[0]
        assert command == "ls"


# ---------------------------------------------------------------------------
# reach job <id>
# ---------------------------------------------------------------------------

class TestJobCmd:
    def test_prints_job_output(self):
        mock_client = MagicMock()
        mock_client.get_job.return_value = _JOB
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["job", "job_1"])
        assert result.exit_code == 0
        assert "ls" in result.output
        assert "file1" in result.output

    def test_http_error_exits_2(self):
        import requests
        mock_resp = MagicMock(status_code=404, text="not found")
        mock_client = MagicMock()
        mock_client.get_job.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["job", "job_1"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# reach history
# ---------------------------------------------------------------------------

class TestHistory:
    def test_shows_jobs_table(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": [_JOB]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["jobs"])
        assert result.exit_code == 0
        assert "ls" in result.output
        assert "SUCCEEDED" in result.output

    def test_no_jobs_prints_message(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["jobs"])
        assert "No jobs" in result.output

    def test_pagination_cursor_shown(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": [_JOB], "next_cursor": "abc123"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["jobs"])
        assert "abc123" in result.output

    def test_agent_filter_passed_to_client(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            runner.invoke(app, ["jobs", "--agent", "agent_b"])
        mock_client.list_jobs.assert_called_once_with(agent_id="agent_b", limit=20, cursor=None)

    def test_alias_shown_in_table(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": [_JOB]}
        with _mock_cfg(aliases={"myprod": "agent_a"}), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["jobs"])
        assert "myprod" in result.output


# ---------------------------------------------------------------------------
# reach profile
# ---------------------------------------------------------------------------

class TestProfile:
    def test_list_shows_profiles(self):
        full = {"active_profile": "default", "profiles": {
            "default": {"api_url": "https://d", "default_agent_id": "agent_a"},
            "prod": {"api_url": "https://p", "default_agent_id": ""},
        }}
        with patch("reach.main.cfg_module.load", return_value=full):
            result = runner.invoke(app, ["profile", "list"])
        assert "default" in result.output
        assert "prod" in result.output

    def test_list_no_profiles_prints_message(self):
        with patch("reach.main.cfg_module.load", return_value={"active_profile": "default", "profiles": {}}):
            result = runner.invoke(app, ["profile", "list"])
        assert "No profiles" in result.output

    def test_use_switches_profile(self):
        with _mock_cfg():
            result = runner.invoke(app, ["profile", "use", "prod"])
        assert result.exit_code == 0
        assert "prod" in result.output

    def test_use_unknown_profile_exits_2(self):
        with patch("reach.main.cfg_module.set_active_profile", side_effect=SystemExit("not found")):
            result = runner.invoke(app, ["profile", "use", "nonexistent"])
        assert result.exit_code == 2

    def test_rename_profile(self):
        with _mock_cfg():
            result = runner.invoke(app, ["profile", "rename", "default", "new"])
        assert result.exit_code == 0
        assert "new" in result.output

    def test_rename_unknown_profile_exits_2(self):
        with patch("reach.main.cfg_module.rename_profile", side_effect=SystemExit("not found")):
            result = runner.invoke(app, ["profile", "rename", "old", "new"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# reach agent-init --for system-prompt (non-interactive path)
# ---------------------------------------------------------------------------

class TestAgentInit:
    def test_invalid_for_value_exits_2(self):
        with _mock_cfg():
            result = runner.invoke(app, ["agent-init", "--for", "invalid"])
        assert result.exit_code == 2

    def test_system_prompt_prints_content(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": [_AGENT]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client), \
             patch("reach.main.Prompt.ask", return_value=""):
            result = runner.invoke(app, ["agent-init", "--for", "system-prompt"])
        assert "Remote Access" in result.output

    def test_mcp_prints_config(self):
        result = runner.invoke(app, ["agent-init", "--for", "mcp"])
        assert result.exit_code == 0
        assert "mcpServers" in result.output


# ---------------------------------------------------------------------------
# _build_agent_context (pure function)
# ---------------------------------------------------------------------------

class TestBuildAgentContext:
    def test_single_agent_no_flag(self):
        from reach.main import _build_agent_context
        with patch("reach.main.cfg_module.list_aliases", return_value={}):
            content = _build_agent_context(
                [{"agent_id": "agent_a", "hostname": "prod-1", "role": "prod", "app_name": ""}],
                "agent_a", "", ""
            )
        assert "agent_a" in content
        assert "--agent" not in content

    def test_multi_agent_includes_flag(self):
        from reach.main import _build_agent_context
        with patch("reach.main.cfg_module.list_aliases", return_value={}):
            content = _build_agent_context(
                [
                    {"agent_id": "agent_a", "hostname": "prod-1", "role": "prod", "app_name": ""},
                    {"agent_id": "agent_b", "hostname": "prod-2", "role": "staging", "app_name": ""},
                ],
                "agent_a", "docker", "extra notes"
            )
        assert "--agent" in content
        assert "docker" in content
        assert "extra notes" in content

    def test_alias_used_in_examples(self):
        from reach.main import _build_agent_context
        with patch("reach.main.cfg_module.list_aliases", return_value={"prod": "agent_a"}):
            content = _build_agent_context(
                [{"agent_id": "agent_a", "hostname": "h", "role": "", "app_name": ""}],
                "agent_a", "", ""
            )
        assert "agent_a" in content

    def test_app_name_adds_docker_examples(self):
        from reach.main import _build_agent_context
        with patch("reach.main.cfg_module.list_aliases", return_value={}):
            content = _build_agent_context(
                [{"agent_id": "agent_a", "hostname": "h", "role": "", "app_name": "my-api"}],
                "agent_a", "", ""
            )
        assert "docker logs my-api" in content


# ---------------------------------------------------------------------------
# _status_color (pure helper)
# ---------------------------------------------------------------------------

class TestStatusColor:
    def test_active_is_green(self):
        from reach.main import _status_color
        assert "green" in _status_color("ACTIVE")

    def test_inactive_is_yellow(self):
        from reach.main import _status_color
        assert "yellow" in _status_color("INACTIVE")

    def test_unknown_returned_as_is(self):
        from reach.main import _status_color
        assert _status_color("WHATEVER") == "WHATEVER"


# ---------------------------------------------------------------------------
# _print_job_result (output formatting)
# ---------------------------------------------------------------------------

class TestPrintJobResult:
    def test_prints_stdout(self, capsys):
        from reach.main import _print_job_result, console
        with patch.object(console, "print") as mock_print:
            _print_job_result(_JOB)
        printed = " ".join(str(c) for c in [a for call in mock_print.call_args_list for a in call.args])
        assert "file1" in printed

    def test_prints_stderr_when_present(self):
        from reach.main import _print_job_result, console
        job_with_stderr = {**_JOB, "stderr": "some error\n", "status": "FAILED"}
        with patch.object(console, "print") as mock_print:
            _print_job_result(job_with_stderr)
        printed = " ".join(str(c) for c in [a for call in mock_print.call_args_list for a in call.args])
        assert "some error" in printed

    def test_no_stdout_section_when_empty(self):
        from reach.main import _print_job_result, console
        job_no_out = {**_JOB, "stdout": "", "stderr": ""}
        with patch.object(console, "print") as mock_print:
            _print_job_result(job_no_out)
        printed = " ".join(str(c) for c in [a for call in mock_print.call_args_list for a in call.args])
        assert "stdout" not in printed


# ---------------------------------------------------------------------------
# reach approvals
# ---------------------------------------------------------------------------

_APPROVAL = {
    "approval_id": "appr_1",
    "command": "docker ps",
    "requester_name": "Alice",
    "requested_by": "user_1",
    "status": "approved",
    "created_at": "2026-06-01T12:00:00+00:00",
    "reviewed_at": "2026-06-01T13:00:00+00:00",
    "expires_at": None,
}


class TestApprovals:
    def _run(self, args, approvals=None, approved_commands=None):
        mock_client = MagicMock()
        mock_client.list_agent_approved.return_value = {
            "approvals": approvals if approvals is not None else [_APPROVAL],
            "approved_commands": approved_commands if approved_commands is not None else ["docker ps"],
        }
        # Build a fresh profile so mutations from other tests (e.g. TestAgentsUse mutates _PROFILE)
        # don't bleed in - always start with agent_a as the default.
        fresh = {"api_url": "https://api.example.com", "api_key": "tok_test", "default_agent_id": "agent_a"}
        with _mock_cfg(profile=fresh), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "list"] + args)
        return result, mock_client

    def test_default_shows_approved_list(self):
        result, client = self._run([])
        assert result.exit_code == 0
        client.list_agent_approved.assert_called_once_with("agent_a", status="approved")
        # Rendered as structured bin / args cells (legacy command split for display).
        assert "docker" in result.output and "ps" in result.output

    def test_pending_flag_calls_pending_status(self):
        result, client = self._run(["--pending"], approvals=[{**_APPROVAL, "status": "pending"}])
        assert result.exit_code == 0
        client.list_agent_approved.assert_called_once_with("agent_a", status="pending")

    def test_denied_flag_calls_denied_status(self):
        result, client = self._run(["--denied"], approvals=[{**_APPROVAL, "status": "denied"}])
        assert result.exit_code == 0
        client.list_agent_approved.assert_called_once_with("agent_a", status="denied")

    def test_expired_flag_calls_expired_status(self):
        past = "2020-01-01T00:00:00+00:00"
        result, client = self._run(["--expired"], approvals=[{**_APPROVAL, "expires_at": past}])
        assert result.exit_code == 0
        client.list_agent_approved.assert_called_once_with("agent_a", status="expired")

    def test_mutual_exclusion_of_flags(self):
        result, _ = self._run(["--pending", "--denied"])
        assert result.exit_code == 2
        assert "only one" in result.output

    def test_host_approval_shows_bin_args_columns(self):
        result, _ = self._run([])
        # host approvals render as structured bin / args, not a flat Command column
        assert "Bin" in result.output and "Args" in result.output
        assert "Command" not in result.output
        assert "Namespace" not in result.output

    def test_host_structured_rule_renders_wildcard(self):
        rule = {
            "host_rule": {"bin": "systemctl", "args": ["restart", "*"]},
            "command": "systemctl restart *",
            "requester_name": "Alice", "status": "approved", "created_at": "2026-06-01T10:00:00Z",
        }
        result, _ = self._run([], approvals=[rule])
        assert result.exit_code == 0
        assert "systemctl" in result.output and "restart" in result.output
        assert "✱" in result.output   # the "*" wildcard arg renders as a dim asterisk

    def test_k8s_approval_renders_structured_rule_columns(self):
        k8s = {
            "k8s_rule": {"verb": "delete", "resource": "pods", "namespace": "team-a", "name": "*"},
            "requester_name": "Alice", "status": "approved", "created_at": "2026-06-01T10:00:00Z",
        }
        result, _ = self._run([], approvals=[k8s])
        assert result.exit_code == 0
        for col in ("Verb", "Resource", "Namespace", "Name"):
            assert col in result.output
        assert "delete" in result.output and "team-a" in result.output
        # k8s rules render under their own header
        assert "Kubernetes rules" in result.output

    def test_k8s_agent_with_helm_host_rule_renders_both_tables(self):
        # A k8s agent can hold kubectl verb rules AND {bin,args} rules (helm etc.);
        # each renders in its own table (mixed-safe).
        mixed = [
            {"k8s_rule": {"verb": "delete", "resource": "pods", "namespace": "team-a", "name": "*"},
             "requester_name": "Alice", "status": "approved", "created_at": "2026-06-01T10:00:00Z"},
            {"host_rule": {"bin": "helm", "args": ["install", "*"]},
             "requester_name": "Bob", "status": "approved", "created_at": "2026-06-01T10:00:00Z"},
        ]
        result, _ = self._run([], approvals=mixed)
        assert result.exit_code == 0
        assert "Kubernetes rules" in result.output       # kubectl verb table
        assert "Verb" in result.output and "Bin" in result.output   # both column sets present
        assert "helm" in result.output and "delete" in result.output

    def test_agent_flag_overrides_default(self):
        result, client = self._run(["--agent", "agent_b"])
        client.list_agent_approved.assert_called_once_with("agent_b", status="approved")

    def test_empty_approved_prints_no_commands_message(self):
        result, _ = self._run([], approvals=[], approved_commands=[])
        assert result.exit_code == 0
        assert "No approved commands" in result.output

    def test_empty_pending_prints_no_pending_message(self):
        result, _ = self._run(["--pending"], approvals=[])
        assert result.exit_code == 0
        assert "No pending" in result.output

    def test_permanent_expiry_shown_for_null_expires_at(self):
        result, _ = self._run([], approvals=[{**_APPROVAL, "expires_at": None}])
        assert "permanent" in result.output

    def test_non_approved_view_shows_status_column(self):
        result, _ = self._run(["--pending"], approvals=[{**_APPROVAL, "status": "pending"}])
        assert "pending" in result.output.lower()

    def test_http_error_exits_2(self):
        import requests as req
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_client.list_agent_approved.side_effect = req.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "list"])
        assert result.exit_code == 2
        assert "401" in result.output


# ---------------------------------------------------------------------------
# _expires_label (pure helper)
# ---------------------------------------------------------------------------

class TestExpiresLabel:
    def test_approved_null_expires_at_returns_permanent(self):
        from reach.main import _expires_label
        assert "permanent" in _expires_label({"expires_at": None, "status": "approved"})

    def test_non_approved_null_expires_at_returns_dash(self):
        from reach.main import _expires_label
        assert "-" in _expires_label({"expires_at": None, "status": "pending"})
        assert "-" in _expires_label({"expires_at": None})

    def test_past_date_shows_expired(self):
        from reach.main import _expires_label
        past = "2020-01-01T00:00:00+00:00"
        assert "(expired)" in _expires_label({"expires_at": past})

    def test_future_days_shows_relative_days(self):
        from datetime import datetime, timezone, timedelta
        from reach.main import _expires_label
        future = (datetime.now(tz=timezone.utc) + timedelta(days=7)).isoformat()
        label = _expires_label({"expires_at": future})
        assert "in" in label
        assert "d" in label
        assert "(expired)" not in label

    def test_future_hours_shows_relative_hours(self):
        from datetime import datetime, timezone, timedelta
        from reach.main import _expires_label
        future = (datetime.now(tz=timezone.utc) + timedelta(hours=6)).isoformat()
        label = _expires_label({"expires_at": future})
        assert "in" in label
        assert "h" in label
        assert "d" not in label

    def test_future_minutes_shows_relative_minutes(self):
        from datetime import datetime, timezone, timedelta
        from reach.main import _expires_label
        future = (datetime.now(tz=timezone.utc) + timedelta(minutes=45)).isoformat()
        label = _expires_label({"expires_at": future})
        assert "in" in label
        assert "m" in label
        assert "h" not in label

    def test_expiring_soon_uses_urgent_markup(self):
        from datetime import datetime, timezone, timedelta
        from reach.main import _expires_label
        future = (datetime.now(tz=timezone.utc) + timedelta(minutes=30)).isoformat()
        label = _expires_label({"expires_at": future})
        assert "red" in label

    def test_expiring_within_2h_uses_yellow_markup(self):
        from datetime import datetime, timezone, timedelta
        from reach.main import _expires_label
        future = (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat()
        label = _expires_label({"expires_at": future})
        assert "yellow" in label


# ---------------------------------------------------------------------------
# reach fleets
# ---------------------------------------------------------------------------

_FLEET = {"fleet_id": "fleet_1", "name": "web-asg", "mode": "approved", "status": "ACTIVE",
          "member_count": 2, "writable": True, "tags": []}
_RO_FLEET = {**_FLEET, "writable": False}
_MEMBER = {"agent_id": "agent_m1", "status": "ACTIVE", "mode": "approved", "hostname": "web-01",
           "agent_version": "0.1.0", "fleet_id": "fleet_1"}


class TestFleetsList:
    def test_shows_fleets_table(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "list"])
        assert result.exit_code == 0
        assert "web-asg" in result.output
        assert "fleet_1" in result.output

    def test_no_fleets_message(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "list"])
        assert "No fleets" in result.output


class TestFleetsShow:
    def test_shows_fleet_detail(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [{**_FLEET, "tags": ["env:prod"]}]}
        mock_client.list_fleet_agents.return_value = {"agents": [
            {"agent_id": "m1", "status": "ACTIVE"}, {"agent_id": "m2", "status": "INACTIVE"}]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "show", "web-asg"])
        assert result.exit_code == 0
        assert "web-asg" in result.output and "env:prod" in result.output
        assert "active" in result.output  # member breakdown

    def test_show_json(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.list_fleet_agents.return_value = {"agents": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["--json", "fleets", "show", "web-asg"])
        assert result.exit_code == 0
        assert json.loads(result.output)["fleet_id"] == "fleet_1"


class TestFleetsAgents:
    def test_lists_members(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.list_fleet_agents.return_value = {"fleet_id": "fleet_1", "fleet_name": "web-asg", "agents": [_MEMBER]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "agents", "web-asg"])
        assert result.exit_code == 0
        assert "agent_m1" in result.output
        mock_client.list_fleet_agents.assert_called_once_with("fleet_1")

    def test_unknown_fleet_exits_2(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "agents", "nope"])
        assert result.exit_code == 2
        assert "Fleet not found" in result.output


class TestFleetsExec:
    def test_confirm_prompt_dispatches(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        preview = {"dry_run": True, "fleet_id": "fleet_1", "fleet_name": "web-asg", "command": "uptime",
                   "mode": "approved", "matched": 1, "wave_size": 1, "wave_strategy": "auto",
                   "failure_policy": "stop", "wave_total": 1, "is_write": False,
                   "approval_required": False, "skipped": []}
        dispatch = {"fleet_id": "fleet_1", "command": "uptime", "dispatched": 1,
                    "jobs": [{"agent_id": "agent_m1", "hostname": "web-01", "job_id": "job_m", "status": "PENDING"}],
                    "skipped": []}
        mock_client.fleet_fanout.side_effect = [preview, dispatch]   # dry-run, then real
        mock_client.get_job.return_value = {"status": "SUCCEEDED", "exit_code": 0, "duration_ms": 10}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "exec", "web-asg", "--", "uptime"], input="y\n")
        assert result.exit_code == 0
        assert "Wave size" in result.output and "Proceed?" in result.output
        mock_client.fleet_fanout.assert_any_call("fleet_1", "uptime", max_targets=None, dry_run=True)
        mock_client.fleet_fanout.assert_any_call("fleet_1", "uptime", max_targets=None)

    def test_declining_confirm_aborts(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.fleet_fanout.return_value = {"dry_run": True, "fleet_name": "web-asg", "command": "uptime",
            "matched": 1, "wave_size": 1, "wave_strategy": "auto", "failure_policy": "stop",
            "wave_total": 1, "skipped": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "exec", "web-asg", "--", "uptime"], input="n\n")
        assert result.exit_code == 2
        # Only the dry-run preview ran; no real dispatch.
        mock_client.fleet_fanout.assert_called_once_with("fleet_1", "uptime", max_targets=None, dry_run=True)

    def test_yes_flag_skips_prompt(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.fleet_fanout.return_value = {"dispatched": 1, "jobs": [
            {"agent_id": "agent_m1", "hostname": "web-01", "job_id": "job_m", "status": "PENDING"}], "skipped": []}
        mock_client.get_job.return_value = {"status": "SUCCEEDED", "exit_code": 0, "duration_ms": 10}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "exec", "web-asg", "-y", "--no-wait", "--", "uptime"])
        assert result.exit_code == 0
        mock_client.fleet_fanout.assert_called_once_with("fleet_1", "uptime", max_targets=None)

    def test_readonly_fleet_blocks(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_RO_FLEET]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "exec", "web-asg", "-y", "--", "uptime"])
        assert result.exit_code == 2
        assert "read-only" in result.output
        mock_client.fleet_fanout.assert_not_called()

    def test_multiword_command_on_default_fleet(self):
        """The papercut fix: `fleets exec -- <multi word cmd>` runs on the `fleets use`
        default (no explicit fleet). Needs the real `--` from argv, so patch sys.argv."""
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.fleet_fanout.return_value = {"dispatched": 1, "jobs": [
            {"agent_id": "agent_m1", "hostname": "web-01", "job_id": "job_m", "status": "PENDING"}], "skipped": []}
        argv = ["reach", "fleets", "exec", "-y", "--no-wait", "--", "systemctl", "restart", "app"]
        prof = {**_PROFILE, "default_fleet": "fleet_1"}
        with _mock_cfg(profile=prof), patch("reach.main.sys.argv", argv), \
                patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "exec", "-y", "--no-wait", "--", "systemctl", "restart", "app"])
        assert result.exit_code == 0
        mock_client.fleet_fanout.assert_called_once_with("fleet_1", "systemctl restart app", max_targets=None)

    def test_multiword_command_with_explicit_fleet(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.fleet_fanout.return_value = {"dispatched": 1, "jobs": [
            {"agent_id": "agent_m1", "hostname": "web-01", "job_id": "job_m", "status": "PENDING"}], "skipped": []}
        argv = ["reach", "fleets", "exec", "web-asg", "-y", "--no-wait", "--", "systemctl", "restart", "app"]
        with _mock_cfg(), patch("reach.main.sys.argv", argv), \
                patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "exec", "web-asg", "-y", "--no-wait", "--", "systemctl", "restart", "app"])
        assert result.exit_code == 0
        mock_client.fleet_fanout.assert_called_once_with("fleet_1", "systemctl restart app", max_targets=None)


class TestSplitFleetCommand:
    @pytest.mark.parametrize("raw, expected", [
        (["web-asg", "--", "systemctl", "restart", "app"], ("web-asg", "systemctl restart app")),
        (["--", "systemctl", "restart", "app"],            (None, "systemctl restart app")),
        (["--", "uptime"],                                 (None, "uptime")),
        (["web-asg", "--", "uptime"],                      ("web-asg", "uptime")),
        (["web-asg", "-y", "--", "systemctl", "restart"],  ("web-asg", "systemctl restart")),
        (["-y", "-t", "30", "web-asg", "--", "uptime"],    ("web-asg", "uptime")),
        (["-y", "--", "uptime"],                           (None, "uptime")),
    ])
    def test_split(self, raw, expected):
        from reach.main import split_fleet_command
        assert split_fleet_command(raw) == expected

    def test_post_exec_tokens(self):
        from reach.main import _post_exec_tokens
        assert _post_exec_tokens(["/x/reach", "fleets", "exec", "--", "uptime"]) == ["--", "uptime"]
        assert _post_exec_tokens(["pytest", "-q"]) is None


class TestFleetsJobs:
    def test_lists_fleet_jobs(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.list_jobs.return_value = {"jobs": [{**_JOB, "agent_hostname": "web-01"}]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "jobs", "web-asg"])
        assert result.exit_code == 0
        assert "web-01" in result.output
        assert "job_1" in result.output            # Job ID column, for `reach job <id>`
        assert "reach job" in result.output        # hint present
        mock_client.list_jobs.assert_called_once_with(fleet_id="fleet_1", limit=20, cursor=None)

    def test_member_filter_lists_one_agent(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.list_fleet_agents.return_value = {"agents": [_MEMBER]}
        mock_client.list_jobs.return_value = {"jobs": [{**_JOB, "agent_hostname": "web-01"}]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "jobs", "web-asg", "--member", "web-01"])
        assert result.exit_code == 0
        mock_client.list_jobs.assert_called_once_with(agent_id="agent_m1", limit=20, cursor=None)

    def test_unknown_member_errors(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.list_fleet_agents.return_value = {"agents": [_MEMBER]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "jobs", "web-asg", "--member", "nope"])
        assert result.exit_code == 2
        assert "not a member" in result.output


class TestFleetsRuns:
    def test_runs_lists_batches(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.list_fleet_runs.return_value = {"runs": [
            {"run_id": "batch_a", "command": "uptime", "created_at": "2026-06-01T10:00:00Z",
             "members": 3, "ok": 2, "failed": 1, "pending": 0},
        ]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "runs", "web-asg"])
        assert result.exit_code == 0
        assert "batch_a" in result.output and "uptime" in result.output
        mock_client.list_fleet_runs.assert_called_once_with("fleet_1", limit=20)

    def test_run_expands_batch(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": [
            {**_JOB, "agent_hostname": "web-01", "run_id": "batch_a"},
        ]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "run", "batch_a"])
        assert result.exit_code == 0
        assert "web-01" in result.output
        mock_client.list_jobs.assert_called_once_with(run_id="batch_a", limit=100)


class TestTagRuns:
    def test_runs_lists_tag_batches(self):
        mock_client = MagicMock()
        mock_client.list_tag_runs.return_value = {"runs": [
            {"run_id": "batch_t", "tag": "env:prod", "command": "systemctl status nginx",
             "created_at": "2026-06-01T10:00:00Z", "members": 2, "ok": 1, "failed": 1, "pending": 0},
        ]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["runs"])
        assert result.exit_code == 0
        # (Rich truncates the wide table at the test console width; content is
        # asserted exactly via the --json path in test_runs_json.)
        assert "Tag runs" in result.output
        mock_client.list_tag_runs.assert_called_once_with(limit=20)

    def test_runs_empty(self):
        mock_client = MagicMock()
        mock_client.list_tag_runs.return_value = {"runs": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["runs"])
        assert result.exit_code == 0
        assert "No tag fan-out runs yet" in result.output

    def test_run_expands_batch_with_tag(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": [
            {**_JOB, "agent_hostname": "prod-cache-01", "run_id": "batch_t", "run_tag": "env:prod"},
        ]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["run", "batch_t"])
        assert result.exit_code == 0
        assert "prod-cache-01" in result.output and "env:prod" in result.output
        mock_client.list_jobs.assert_called_once_with(run_id="batch_t", limit=100)

    def test_runs_json(self):
        mock_client = MagicMock()
        mock_client.list_tag_runs.return_value = {"runs": [{"run_id": "batch_t", "tag": "env:prod"}]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["--json", "runs"])
        assert result.exit_code == 0
        assert '"batch_t"' in result.output and '"env:prod"' in result.output


class TestStagedRollout:
    def test_exec_shows_staged_output_when_server_stages(self):
        # Staging is policy-driven server-side; the CLI just reflects the response.
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.fleet_fanout.return_value = {
            "run_id": "run_s", "dispatched": 2, "total": 5, "wave_total": 3,
            "jobs": [{"agent_id": "a1", "hostname": "web-01", "job_id": "j1", "status": "PENDING"}],
            "skipped": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "exec", "web-asg", "-y", "--", "deploy.sh"])
        assert result.exit_code == 0
        mock_client.fleet_fanout.assert_called_once_with("fleet_1", "deploy.sh", max_targets=None)
        assert "Staged rollout" in result.output and "wave 1 of 3" in result.output

    def test_runs_status_shows_waves(self):
        mock_client = MagicMock()
        mock_client.get_run.return_value = {
            "run_id": "run_s", "command": "deploy.sh", "state": "paused",
            "counts": {"ok": 2, "failed": 0, "pending": 3, "running": 0},
            "wave_total": 3, "current_wave": 1, "staged": 3, "total": 6,
            "rollout": {"waves": [2, 2, 2], "mode": "manual", "on_failure": "continue"}}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["runs", "status", "run_s"])
        assert result.exit_code == 0
        assert "Wave 2 of 3" in result.output
        # Wave size + strategy + failure policy are all shown.
        assert "Wave size:" in result.output and "2" in result.output
        assert "MANUAL" in result.output and "CONTINUE" in result.output
        mock_client.get_run.assert_called_once_with("run_s")

    def test_runs_pause(self):
        mock_client = MagicMock()
        mock_client.pause_run.return_value = {"run_id": "run_s", "state": "paused"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["runs", "pause", "run_s"])
        assert result.exit_code == 0
        mock_client.pause_run.assert_called_once_with("run_s")
        assert "paused" in result.output

    def test_runs_resume(self):
        mock_client = MagicMock()
        mock_client.resume_run.return_value = {"run_id": "run_s", "state": "running"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["runs", "resume", "run_s"])
        assert result.exit_code == 0
        mock_client.resume_run.assert_called_once_with("run_s")

    def test_runs_cancel(self):
        mock_client = MagicMock()
        mock_client.cancel_run.return_value = {"run_id": "run_s", "state": "canceled", "canceled": 3}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["runs", "cancel", "run_s"])
        assert result.exit_code == 0
        mock_client.cancel_run.assert_called_once_with("run_s")
        assert "canceled" in result.output


class TestStandaloneSeparation:
    def test_history_hides_fleet_member_jobs(self):
        jobs = [
            {**_JOB, "job_id": "job_s", "agent_id": "agent_a", "agent_fleet_id": None},
            {**_JOB, "job_id": "job_m", "agent_id": "agent_m1", "agent_fleet_id": "fleet_1", "command": "fleetcmd"},
        ]
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": jobs}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["jobs"])
        assert result.exit_code == 0
        assert "fleetcmd" not in result.output  # fleet-member job hidden
        assert "Fleet-member jobs are hidden" in result.output

    def test_jobs_agent_member_shows_that_members_jobs(self):
        # An explicit --agent <member> query is bounded to one agent, so it shows that
        # member's jobs directly (no redirect); only the unfiltered list hides members.
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {
            "jobs": [{**_JOB, "agent_id": "agent_m1", "agent_fleet_id": "fleet_1", "command": "membercmd"}],
            "agent_fleet_id": "fleet_1",
        }
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["jobs", "--agent", "agent_m1"])
        assert result.exit_code == 0
        assert "membercmd" in result.output

    def test_approvals_member_redirects(self):
        mock_client = MagicMock()
        mock_client.list_agent_approved.return_value = {"approvals": [], "agent_fleet_id": "fleet_1"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "list", "--agent", "agent_m1"])
        assert result.exit_code == 2
        assert "reach fleets approvals fleet_1" in result.output


class TestFleetsApprovals:
    def test_approved_lists_commands(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.list_fleet_approved.return_value = {
            "fleet_id": "fleet_1", "fleet_name": "web-asg",
            "approved_commands": ["docker restart web"],
            "approvals": [{"host_rule": {"bin": "docker", "args": ["restart", "web"]},
                           "command": "docker restart web", "status": "approved", "requested_by": "alice"}],
        }
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "approvals", "list", "web-asg"])
        assert result.exit_code == 0
        # structured bin / args rendering (fleets are host-only)
        assert "docker" in result.output and "restart web" in result.output
        mock_client.list_fleet_approved.assert_called_once_with("fleet_1", status="approved")

    def test_pending_flag_passes_status(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.list_fleet_approved.return_value = {"approvals": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "approvals", "list", "web-asg", "--pending"])
        assert result.exit_code == 0
        mock_client.list_fleet_approved.assert_called_once_with("fleet_1", status="pending")

    def test_multiple_status_flags_rejected(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "approvals", "list", "web-asg", "--pending", "--denied"])
        assert result.exit_code == 2


class TestAgentsListExcludesMembers:
    def test_agents_list_shows_only_standalone(self):
        member = {**_AGENT, "agent_id": "agent_m1", "fleet_id": "fleet_1", "hostname": "web-01"}
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": [_AGENT, member]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["agents", "list"])
        assert result.exit_code == 0
        assert "prod-1" in result.output      # standalone shown
        assert "web-01" not in result.output  # fleet member hidden

    def test_all_members_shows_empty_message(self):
        member = {**_AGENT, "agent_id": "agent_m1", "fleet_id": "fleet_1", "hostname": "web-01"}
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": [member]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["agents", "list"])
        assert "No standalone agents" in result.output


# ---------------------------------------------------------------------------
# New: --json, agents show, approvals mutations, exec --tag, fleets use
# ---------------------------------------------------------------------------

class TestJsonOutput:
    def test_whoami_json(self):
        mock_client = MagicMock()
        mock_client.get_me.return_value = {"user_id": "u1", "role": "admin"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["--json", "whoami"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {"user_id": "u1", "role": "admin"}

    def test_agents_list_json(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = {"agents": [_AGENT]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["--json", "agents", "list"])
        assert result.exit_code == 0
        assert json.loads(result.output)["agents"][0]["agent_id"] == "agent_a"


class TestAgentsShow:
    def test_shows_detail(self):
        mock_client = MagicMock()
        mock_client.get_agent.return_value = {**_AGENT, "tags": ["env:prod"]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["agents", "show", "agent_a"])
        assert result.exit_code == 0
        assert "prod-1" in result.output and "env:prod" in result.output
        mock_client.get_agent.assert_called_once_with("agent_a")


class TestApprovalsMutations:
    def test_request_for_agent(self):
        mock_client = MagicMock()
        mock_client.get_agent.return_value = {"type": "host"}
        mock_client.create_approval.return_value = {"approval_id": "appr_1", "status": "pending"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "request", "docker ps", "--agent", "agent_a"])
        assert result.exit_code == 0
        mock_client.create_approval.assert_called_once_with("docker ps", agent_id="agent_a", duration=None)

    def test_request_k8s_derives_kubectl_rule(self):
        # A k8s agent can't take a bare command string; the CLI derives a k8s_rule.
        mock_client = MagicMock()
        mock_client.get_agent.return_value = {"type": "k8s"}
        mock_client.create_approval.return_value = {"approval_id": "appr_1", "status": "approved"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "request",
                                         "kubectl delete pod foo -n reach", "--agent", "agent_k"])
        assert result.exit_code == 0
        mock_client.create_approval.assert_called_once_with(
            agent_id="agent_k", duration=None,
            k8s_rule={"verb": "delete", "resource": "pods", "namespace": "reach", "name": "foo"},
            host_rule=None)

    def test_request_k8s_nonkubectl_derives_host_rule(self):
        mock_client = MagicMock()
        mock_client.get_agent.return_value = {"type": "k8s"}
        mock_client.create_approval.return_value = {"approval_id": "appr_1", "status": "approved"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "request",
                                         "helm upgrade reach ./chart", "--agent", "agent_k"])
        assert result.exit_code == 0
        mock_client.create_approval.assert_called_once_with(
            agent_id="agent_k", duration=None,
            k8s_rule=None,
            host_rule={"bin": "helm", "args": ["upgrade", "reach", "./chart"]})

    def test_request_k8s_read_command_errors_client_side(self):
        # A read has no approvable write - fail in the CLI, never hit the backend.
        mock_client = MagicMock()
        mock_client.get_agent.return_value = {"type": "k8s"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "request",
                                         "kubectl get pods", "--agent", "agent_k"])
        assert result.exit_code != 0
        mock_client.create_approval.assert_not_called()

    def test_request_has_no_fleet_flag(self):
        # Fleet approval requests are a separate command under `reach fleets approvals`.
        mock_client = MagicMock()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "request", "docker ps", "--fleet", "web-asg"])
        assert result.exit_code != 0  # --fleet is not a valid option here

    def test_fleet_request_under_fleets(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        mock_client.create_approval.return_value = {"approval_id": "appr_1", "status": "approved"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "approvals", "request", "web-asg", "docker restart web"])
        assert result.exit_code == 0
        mock_client.create_approval.assert_called_once_with("docker restart web", fleet_id="fleet_1", duration=None)

    def test_approve(self):
        mock_client = MagicMock()
        mock_client.approve_approval.return_value = {"status": "approved"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "approve", "appr_1", "--duration", "8h"])
        assert result.exit_code == 0
        mock_client.approve_approval.assert_called_once_with("appr_1", duration="8h")

    def test_deny(self):
        mock_client = MagicMock()
        mock_client.deny_approval.return_value = {"status": "denied"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "deny", "appr_1"])
        assert result.exit_code == 0
        mock_client.deny_approval.assert_called_once_with("appr_1")


class TestExecTagFanout:
    def _agents(self, *specs):
        return {"agents": list(specs)}

    def test_confirm_and_dispatch_via_server(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = self._agents(
            {"agent_id": "agent_a", "hostname": "prod-1", "status": "ACTIVE", "writable": True, "type": "host"},
            {"agent_id": "agent_b", "hostname": "prod-2", "status": "ACTIVE", "writable": True, "type": "host"},
        )
        preview = {"dry_run": True, "tag": "env:prod", "type": "host", "command": "uptime",
                   "matched": 2, "wave_size": 2, "wave_strategy": "auto", "failure_policy": "continue",
                   "wave_total": 1, "is_write": False, "skipped": []}
        dispatch = {
            "tag": "env:prod", "type": "host", "run_id": "batch_a", "dispatched": 2, "skipped": [],
            "jobs": [{"job_id": "job_a", "agent_id": "agent_a", "hostname": "prod-1"},
                     {"job_id": "job_b", "agent_id": "agent_b", "hostname": "prod-2"}],
        }
        mock_client.fanout_by_tag.side_effect = [preview, dispatch]   # dry-run, then real
        mock_client.get_job.return_value = {"status": "SUCCEEDED", "exit_code": 0, "duration_ms": 5}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--tag", "env:prod", "--", "uptime"], input="y\n")
        assert result.exit_code == 0
        assert "Wave size" in result.output and "Proceed?" in result.output
        mock_client.fanout_by_tag.assert_any_call("env:prod", "uptime", agent_type="host", dry_run=True)
        mock_client.fanout_by_tag.assert_any_call("env:prod", "uptime", agent_type="host")

    def test_decline_aborts_exit_2(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = self._agents(
            {"agent_id": "agent_a", "hostname": "prod-1", "status": "ACTIVE", "writable": True, "type": "host"})
        mock_client.fanout_by_tag.return_value = {"dry_run": True, "tag": "env:prod", "type": "host",
            "command": "uptime", "matched": 1, "wave_size": 1, "wave_strategy": "auto",
            "failure_policy": "stop", "wave_total": 1, "skipped": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--tag", "env:prod", "--", "uptime"], input="n\n")
        assert result.exit_code == 2
        # Only the dry-run preview ran; no real dispatch.
        mock_client.fanout_by_tag.assert_called_once_with("env:prod", "uptime", agent_type="host", dry_run=True)

    def test_mixed_types_requires_type(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = self._agents(
            {"agent_id": "agent_a", "hostname": "prod-1", "status": "ACTIVE", "writable": True, "type": "host"},
            {"agent_id": "k1", "hostname": "cluster-1", "status": "ACTIVE", "writable": True, "type": "k8s"})
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--tag", "env:prod", "-y", "--", "uptime"])
        assert result.exit_code == 2
        assert "both host and k8s" in result.output
        mock_client.fanout_by_tag.assert_not_called()

    def test_type_k8s_selects_k8s(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = self._agents(
            {"agent_id": "agent_a", "status": "ACTIVE", "writable": True, "type": "host"},
            {"agent_id": "k1", "hostname": "cluster-1", "status": "ACTIVE", "writable": True, "type": "k8s"})
        mock_client.fanout_by_tag.return_value = {"type": "k8s", "run_id": "b", "dispatched": 1, "skipped": [],
                                                  "jobs": [{"job_id": "j1", "agent_id": "k1", "hostname": "cluster-1"}]}
        mock_client.get_job.return_value = {"status": "SUCCEEDED", "exit_code": 0, "duration_ms": 5}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--tag", "env:prod", "--type", "k8s", "-y", "--", "kubectl get pods"])
        assert result.exit_code == 0
        mock_client.fanout_by_tag.assert_called_once_with("env:prod", "kubectl get pods", agent_type="k8s")

    def test_no_matching_agents_exit_2(self):
        mock_client = MagicMock()
        mock_client.list_agents.return_value = self._agents(
            {"agent_id": "agent_m", "status": "ACTIVE", "writable": True, "fleet_id": "fleet_1", "type": "host"})
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--tag", "env:prod", "-y", "--", "uptime"])
        assert result.exit_code == 2  # fleet member excluded -> no standalone targets


class TestFleetsUse:
    def test_sets_default_fleet(self):
        mock_client = MagicMock()
        mock_client.list_fleets.return_value = {"fleets": [_FLEET]}
        saved = {}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client), \
             patch("reach.main.cfg_module.save_profile", side_effect=lambda d: saved.update(d)):
            result = runner.invoke(app, ["fleets", "use", "web-asg"])
        assert result.exit_code == 0
        assert saved.get("default_fleet") == "fleet_1"


class TestFriendlyErrorsAndJson:
    def test_http_error_surfaces_api_message(self):
        import requests
        resp = MagicMock(status_code=404)
        resp.json.return_value = {"error": "agent not found"}
        mock_client = MagicMock()
        mock_client.get_agent.side_effect = requests.HTTPError(response=resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["agents", "show", "nope"])
        assert result.exit_code == 2
        assert "agent not found" in result.output   # API message, not raw JSON body
        assert "HTTP 404" in result.output

    def test_json_error_is_parseable(self):
        import requests
        resp = MagicMock(status_code=403)
        resp.json.return_value = {"error": "read-only access"}
        mock_client = MagicMock()
        mock_client.get_agent.side_effect = requests.HTTPError(response=resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["--json", "agents", "show", "x"])
        assert result.exit_code == 2
        assert json.loads(result.output)["error"].startswith("read-only access")

    def test_connection_error_is_friendly_not_traceback(self):
        # Backend down / wrong URL: friendly message + exit 2, never a stack trace.
        import requests
        req = MagicMock(url="http://localhost:9999/agents")
        mock_client = MagicMock()
        mock_client.list_agents.side_effect = requests.ConnectionError(request=req)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["agents", "list"])
        assert result.exit_code == 2
        assert "cannot reach the backend" in result.output
        assert "localhost:9999" in result.output
        assert "Traceback" not in result.output

    def test_timeout_is_friendly(self):
        import requests
        req = MagicMock(url="http://localhost:8080/fleets")
        mock_client = MagicMock()
        mock_client.list_fleets.side_effect = requests.Timeout(request=req)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["fleets", "list"])
        assert result.exit_code == 2
        assert "timed out" in result.output.lower()

    def test_jobs_json_preserves_envelope(self):
        # --json emits the full API envelope (e.g. next_cursor), collection filtered to standalone.
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {
            "jobs": [{**_JOB, "agent_fleet_id": None}, {**_JOB, "job_id": "jm", "agent_fleet_id": "fleet_1"}],
            "next_cursor": "abc",
        }
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["--json", "jobs"])
        out = json.loads(result.output)
        assert out["next_cursor"] == "abc"                 # envelope preserved
        assert [j["job_id"] for j in out["jobs"]] == ["job_1"]  # fleet-member job filtered out

    def test_exec_json_emits_result(self):
        mock_client = MagicMock()
        mock_client.create_job.return_value = {"job_id": "job_1"}
        mock_client.get_job.return_value = {**_JOB, "status": "SUCCEEDED", "exit_code": 0}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["--json", "exec", "--", "ls"])
        assert result.exit_code == 0
        assert json.loads(result.output)["job_id"] == "job_1"
