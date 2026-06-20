"""
Tests for reach/client.py - ReachClient HTTP wrapper.
Each method is tested by mocking the requests.Session so no real HTTP is made.
"""
import pytest
from unittest.mock import MagicMock, patch

from reach.client import ReachClient


def _make_client(api_url="https://api.example.com", api_key="tok_secret"):
    return ReachClient(api_url, api_key)


def _mock_response(json_data=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_strips_trailing_slash_from_api_url(self):
        c = _make_client(api_url="https://api.example.com/")
        assert c.api_url == "https://api.example.com"

    def test_no_slash_left_intact(self):
        c = _make_client(api_url="https://api.example.com")
        assert c.api_url == "https://api.example.com"

    def test_sets_authorization_header(self):
        c = _make_client(api_key="tok_abc")
        assert c.session.headers.get("Authorization") == "Bearer tok_abc"

    def test_sets_content_type_header(self):
        c = _make_client()
        assert c.session.headers.get("Content-Type") == "application/json"


# ---------------------------------------------------------------------------
# _url
# ---------------------------------------------------------------------------

class TestUrl:
    def test_concatenates_base_and_path(self):
        c = _make_client(api_url="https://api.example.com")
        assert c._url("/jobs") == "https://api.example.com/jobs"

    def test_nested_path(self):
        c = _make_client(api_url="https://api.example.com")
        assert c._url("/agents/agent_a") == "https://api.example.com/agents/agent_a"


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------

class TestCreateJob:
    def test_posts_to_jobs_endpoint(self):
        c = _make_client()
        resp = _mock_response({"job_id": "job_1", "status": "PENDING"})
        c.session.post = MagicMock(return_value=resp)
        result = c.create_job("agent_a", "ls -la")
        c.session.post.assert_called_once_with(
            "https://api.example.com/jobs",
            json={"agent_id": "agent_a", "command": "ls -la"},
            timeout=15,
        )
        assert result == {"job_id": "job_1", "status": "PENDING"}

    def test_calls_raise_for_status(self):
        c = _make_client()
        resp = _mock_response()
        c.session.post = MagicMock(return_value=resp)
        c.create_job("agent_a", "ls")
        resp.raise_for_status.assert_called_once()


# ---------------------------------------------------------------------------
# get_job
# ---------------------------------------------------------------------------

class TestGetJob:
    def test_gets_job_by_id(self):
        c = _make_client()
        resp = _mock_response({"job_id": "job_1", "status": "SUCCEEDED"})
        c.session.get = MagicMock(return_value=resp)
        result = c.get_job("job_1")
        c.session.get.assert_called_once_with(
            "https://api.example.com/jobs/job_1",
            timeout=15,
        )
        assert result["job_id"] == "job_1"

    def test_calls_raise_for_status(self):
        c = _make_client()
        resp = _mock_response()
        c.session.get = MagicMock(return_value=resp)
        c.get_job("job_1")
        resp.raise_for_status.assert_called_once()


# ---------------------------------------------------------------------------
# get_agent
# ---------------------------------------------------------------------------

class TestGetAgent:
    def test_gets_agent_by_id(self):
        c = _make_client()
        resp = _mock_response({"agent_id": "agent_a", "status": "ACTIVE"})
        c.session.get = MagicMock(return_value=resp)
        result = c.get_agent("agent_a")
        c.session.get.assert_called_once_with(
            "https://api.example.com/agents/agent_a",
            timeout=15,
        )
        assert result["agent_id"] == "agent_a"

    def test_calls_raise_for_status(self):
        c = _make_client()
        resp = _mock_response()
        c.session.get = MagicMock(return_value=resp)
        c.get_agent("agent_a")
        resp.raise_for_status.assert_called_once()


# ---------------------------------------------------------------------------
# list_agents
# ---------------------------------------------------------------------------

class TestListAgents:
    def test_lists_all_agents_without_tag(self):
        c = _make_client()
        resp = _mock_response({"agents": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_agents()
        c.session.get.assert_called_once_with(
            "https://api.example.com/agents",
            params={},
            timeout=15,
        )

    def test_passes_tag_as_query_param(self):
        c = _make_client()
        resp = _mock_response({"agents": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_agents(tag="env:prod")
        c.session.get.assert_called_once_with(
            "https://api.example.com/agents",
            params={"tag": "env:prod"},
            timeout=15,
        )

    def test_returns_agents_list(self):
        c = _make_client()
        agents = [{"agent_id": "a1"}, {"agent_id": "a2"}]
        c.session.get = MagicMock(return_value=_mock_response({"agents": agents}))
        assert c.list_agents()["agents"] == agents

    def test_calls_raise_for_status(self):
        c = _make_client()
        resp = _mock_response({"agents": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_agents()
        resp.raise_for_status.assert_called_once()


# ---------------------------------------------------------------------------
# get_me
# ---------------------------------------------------------------------------

class TestGetMe:
    def test_gets_me(self):
        c = _make_client()
        resp = _mock_response({"user_id": "u1", "tenant_id": "t1"})
        c.session.get = MagicMock(return_value=resp)
        result = c.get_me()
        c.session.get.assert_called_once_with(
            "https://api.example.com/me",
            timeout=15,
        )
        assert result["user_id"] == "u1"

    def test_calls_raise_for_status(self):
        c = _make_client()
        resp = _mock_response()
        c.session.get = MagicMock(return_value=resp)
        c.get_me()
        resp.raise_for_status.assert_called_once()


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------

class TestListJobs:
    def test_default_params(self):
        c = _make_client()
        resp = _mock_response({"jobs": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_jobs()
        c.session.get.assert_called_once_with(
            "https://api.example.com/jobs",
            params={"limit": 20},
            timeout=15,
        )

    def test_passes_agent_id_when_given(self):
        c = _make_client()
        resp = _mock_response({"jobs": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_jobs(agent_id="agent_a")
        call_params = c.session.get.call_args[1]["params"]
        assert call_params["agent_id"] == "agent_a"

    def test_passes_cursor_when_given(self):
        c = _make_client()
        resp = _mock_response({"jobs": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_jobs(cursor="abc123")
        call_params = c.session.get.call_args[1]["params"]
        assert call_params["cursor"] == "abc123"

    def test_custom_limit(self):
        c = _make_client()
        resp = _mock_response({"jobs": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_jobs(limit=50)
        call_params = c.session.get.call_args[1]["params"]
        assert call_params["limit"] == 50

    def test_no_agent_id_in_params_when_not_given(self):
        c = _make_client()
        resp = _mock_response({"jobs": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_jobs()
        call_params = c.session.get.call_args[1]["params"]
        assert "agent_id" not in call_params

    def test_no_cursor_in_params_when_not_given(self):
        c = _make_client()
        resp = _mock_response({"jobs": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_jobs()
        call_params = c.session.get.call_args[1]["params"]
        assert "cursor" not in call_params

    def test_calls_raise_for_status(self):
        c = _make_client()
        resp = _mock_response({"jobs": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_jobs()
        resp.raise_for_status.assert_called_once()


# ---------------------------------------------------------------------------
# list_agent_approved
# ---------------------------------------------------------------------------

class TestListAgentApproved:
    def test_gets_approved_commands_for_agent(self):
        c = _make_client()
        resp = _mock_response({"approvals": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_agent_approved("agent_a")
        c.session.get.assert_called_once_with(
            "https://api.example.com/agents/agent_a/approved-commands",
            params={"status": "approved"},
            timeout=15,
        )

    def test_custom_status(self):
        c = _make_client()
        resp = _mock_response({"approvals": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_agent_approved("agent_a", status="pending")
        call_params = c.session.get.call_args[1]["params"]
        assert call_params["status"] == "pending"

    def test_calls_raise_for_status(self):
        c = _make_client()
        resp = _mock_response({"approvals": []})
        c.session.get = MagicMock(return_value=resp)
        c.list_agent_approved("agent_a")
        resp.raise_for_status.assert_called_once()
