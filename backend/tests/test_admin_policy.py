import json
import pytest
from unittest.mock import patch

from handlers.admin_policy import (
    handle_add_command,
    handle_get_policy,
    handle_remove_command,
    handle_set_mode,
)

ADMIN = "test-admin-token"
AGENT_ID = "agent_a"

_AGENT = {"agent_id": AGENT_ID, "mode": "wild", "approved_commands": []}
_AGENT_APPROVED = {"agent_id": AGENT_ID, "mode": "approved", "approved_commands": ["docker ps", "git status"]}


class TestGetPolicy:
    def test_unauthorized(self):
        r = handle_get_policy(AGENT_ID, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_get_policy(AGENT_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_returns_policy(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT_APPROVED
            r = handle_get_policy(AGENT_ID, ADMIN)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["mode"] == "approved"
        assert body["approved_commands"] == ["docker ps", "git status"]

    def test_defaults_mode_to_wild(self):
        agent_no_mode = {"agent_id": AGENT_ID}
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = agent_no_mode
            r = handle_get_policy(AGENT_ID, ADMIN)
        assert json.loads(r["body"])["mode"] == "wild"


class TestSetMode:
    def test_unauthorized(self):
        r = handle_set_mode(AGENT_ID, {"mode": "readonly"}, "wrong")
        assert r["statusCode"] == 401

    def test_invalid_mode(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT
            r = handle_set_mode(AGENT_ID, {"mode": "superuser"}, ADMIN)
        assert r["statusCode"] == 400

    def test_missing_mode(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT
            r = handle_set_mode(AGENT_ID, {}, ADMIN)
        assert r["statusCode"] == 400

    def test_agent_not_found(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_set_mode(AGENT_ID, {"mode": "readonly"}, ADMIN)
        assert r["statusCode"] == 404

    @pytest.mark.parametrize("mode", ["wild", "readonly", "approved"])
    def test_valid_modes(self, mode):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT
            r = handle_set_mode(AGENT_ID, {"mode": mode}, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["mode"] == mode

    def test_calls_update_policy(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT
            handle_set_mode(AGENT_ID, {"mode": "readonly"}, ADMIN)
        ar.update_policy.assert_called_once_with(AGENT_ID, "readonly", [])


class TestAddCommand:
    def test_unauthorized(self):
        r = handle_add_command(AGENT_ID, {"commands": ["docker ps"]}, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_add_command(AGENT_ID, {"commands": ["docker ps"]}, ADMIN)
        assert r["statusCode"] == 404

    def test_empty_commands_rejected(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT
            r = handle_add_command(AGENT_ID, {"commands": []}, ADMIN)
        assert r["statusCode"] == 400

    def test_adds_new_commands(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT
            r = handle_add_command(AGENT_ID, {"commands": ["docker ps"]}, ADMIN)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert "docker ps" in body["approved_commands"]
        assert body["added"] == ["docker ps"]

    def test_already_existing_not_duplicated(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT_APPROVED
            r = handle_add_command(AGENT_ID, {"commands": ["docker ps"]}, ADMIN)
        body = json.loads(r["body"])
        assert body["added"] == []
        assert body["already_exists"] == ["docker ps"]
        assert body["approved_commands"].count("docker ps") == 1

    def test_accepts_string_as_single_command(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT
            r = handle_add_command(AGENT_ID, {"commands": "docker ps"}, ADMIN)
        assert r["statusCode"] == 200


class TestRemoveCommand:
    def test_unauthorized(self):
        r = handle_remove_command(AGENT_ID, {"commands": ["docker ps"]}, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = None
            r = handle_remove_command(AGENT_ID, {"commands": ["docker ps"]}, ADMIN)
        assert r["statusCode"] == 404

    def test_empty_commands_rejected(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT_APPROVED
            r = handle_remove_command(AGENT_ID, {"commands": []}, ADMIN)
        assert r["statusCode"] == 400

    def test_removes_existing_command(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT_APPROVED
            r = handle_remove_command(AGENT_ID, {"commands": ["docker ps"]}, ADMIN)
        body = json.loads(r["body"])
        assert "docker ps" not in body["approved_commands"]
        assert body["removed"] == ["docker ps"]
        assert body["not_found"] == []

    def test_nonexistent_command_reported(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT_APPROVED
            r = handle_remove_command(AGENT_ID, {"commands": ["kubectl get pods"]}, ADMIN)
        body = json.loads(r["body"])
        assert body["not_found"] == ["kubectl get pods"]
        assert body["removed"] == []

    def test_accepts_string_as_single_command(self):
        with patch("handlers.admin_policy.agents_repo") as ar:
            ar.get.return_value = _AGENT_APPROVED
            r = handle_remove_command(AGENT_ID, {"commands": "docker ps"}, ADMIN)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["removed"] == ["docker ps"]
