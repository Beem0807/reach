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

    def test_exits_1_when_no_config(self):
        with patch.multiple("reach.main.cfg_module",
                            active_profile_name=MagicMock(return_value="default"),
                            load_profile=MagicMock(return_value={})):
            result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 1

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

    def test_http_error_exits_1(self):
        import requests
        mock_resp = MagicMock(status_code=401, text="unauthorized")
        mock_client = MagicMock()
        mock_client.get_me.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 1
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

    def test_http_error_exits_1(self):
        import requests
        mock_resp = MagicMock(status_code=404, text="not found")
        mock_client = MagicMock()
        mock_client.get_agent.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 1


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
        assert "No agents" in result.output

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

    def test_remove_missing_alias_exits_1(self):
        with patch.multiple("reach.main.cfg_module",
                            remove_alias=MagicMock(return_value=False),
                            resolve_agent=MagicMock(side_effect=lambda x: x)):
            result = runner.invoke(app, ["alias", "remove", "nonexistent"])
        assert result.exit_code == 1

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

    def test_no_command_exits_1(self):
        with _mock_cfg():
            result = runner.invoke(app, ["exec"])
        assert result.exit_code == 1

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
        mock_client.create_job.assert_called_once_with("agent_b", "ls")

    def test_multi_word_command_joined(self):
        mock_client = self._client()
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            runner.invoke(app, ["exec", "--", "docker", "ps", "-a"])
        _, command = mock_client.create_job.call_args[0]
        assert command == "docker ps -a"

    def test_http_error_on_create_exits_1(self):
        import requests
        mock_client = MagicMock()
        mock_resp = MagicMock(status_code=403, text="forbidden")
        mock_client.create_job.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["exec", "--", "ls"])
        assert result.exit_code == 1

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

    def test_http_error_exits_1(self):
        import requests
        mock_resp = MagicMock(status_code=404, text="not found")
        mock_client = MagicMock()
        mock_client.get_job.side_effect = requests.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["job", "job_1"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# reach history
# ---------------------------------------------------------------------------

class TestHistory:
    def test_shows_jobs_table(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": [_JOB]}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["history"])
        assert result.exit_code == 0
        assert "ls" in result.output
        assert "SUCCEEDED" in result.output

    def test_no_jobs_prints_message(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["history"])
        assert "No jobs" in result.output

    def test_pagination_cursor_shown(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": [_JOB], "next_cursor": "abc123"}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["history"])
        assert "abc123" in result.output

    def test_agent_filter_passed_to_client(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": []}
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            runner.invoke(app, ["history", "--agent", "agent_b"])
        mock_client.list_jobs.assert_called_once_with(agent_id="agent_b", limit=20, cursor=None)

    def test_alias_shown_in_table(self):
        mock_client = MagicMock()
        mock_client.list_jobs.return_value = {"jobs": [_JOB]}
        with _mock_cfg(aliases={"myprod": "agent_a"}), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["history"])
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

    def test_use_unknown_profile_exits_1(self):
        with patch("reach.main.cfg_module.set_active_profile", side_effect=SystemExit("not found")):
            result = runner.invoke(app, ["profile", "use", "nonexistent"])
        assert result.exit_code == 1

    def test_rename_profile(self):
        with _mock_cfg():
            result = runner.invoke(app, ["profile", "rename", "default", "new"])
        assert result.exit_code == 0
        assert "new" in result.output

    def test_rename_unknown_profile_exits_1(self):
        with patch("reach.main.cfg_module.rename_profile", side_effect=SystemExit("not found")):
            result = runner.invoke(app, ["profile", "rename", "old", "new"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# reach agent-init --for system-prompt (non-interactive path)
# ---------------------------------------------------------------------------

class TestAgentInit:
    def test_invalid_for_value_exits_1(self):
        with _mock_cfg():
            result = runner.invoke(app, ["agent-init", "--for", "invalid"])
        assert result.exit_code == 1

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
        assert "docker ps" in result.output

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
        assert result.exit_code == 1
        assert "only one" in result.output

    def test_host_approval_shows_command_column(self):
        result, _ = self._run([])
        assert "Command" in result.output
        # host view has no structured-rule columns
        assert "Namespace" not in result.output

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
        # k8s view does not use the flat Command column
        assert "Kubernetes agent" in result.output

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

    def test_http_error_exits_1(self):
        import requests as req
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_client.list_agent_approved.side_effect = req.HTTPError(response=mock_resp)
        with _mock_cfg(), patch("reach.main.ReachClient", return_value=mock_client):
            result = runner.invoke(app, ["approvals", "list"])
        assert result.exit_code == 1
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
