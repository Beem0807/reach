import json
import pytest
from unittest.mock import patch

from handlers.admin_policy import (
    handle_get_policy,
    handle_set_mode,
)

ADMIN = "test-admin-token"
AGENT_ID = "agent_a"

_AGENT = {"agent_id": AGENT_ID, "mode": "wild"}
_AGENT_APPROVED = {"agent_id": AGENT_ID, "mode": "approved"}
_APPROVALS = [
    {"approval_id": "appr_1", "command": "docker ps", "status": "approved"},
    {"approval_id": "appr_2", "command": "git status", "status": "approved"},
]


class TestGetPolicy:
    def test_unauthorized(self):
        r = handle_get_policy(AGENT_ID, "wrong")
        assert r["statusCode"] == 401

    def test_agent_not_found(self):
        with patch("handlers.admin_policy.agents_repo") as ar, \
             patch("handlers.admin_policy.approvals_repo") as apr:
            ar.get.return_value = None
            r = handle_get_policy(AGENT_ID, ADMIN)
        assert r["statusCode"] == 404

    def test_returns_policy(self):
        with patch("handlers.admin_policy.agents_repo") as ar, \
             patch("handlers.admin_policy.approvals_repo") as apr:
            ar.get.return_value = _AGENT_APPROVED
            apr.list_by_agent.return_value = _APPROVALS
            r = handle_get_policy(AGENT_ID, ADMIN)
        assert r["statusCode"] == 200
        body = json.loads(r["body"])
        assert body["mode"] == "approved"
        assert body["approved_commands"] == ["docker ps", "git status"]

    def test_defaults_mode_to_wild(self):
        agent_no_mode = {"agent_id": AGENT_ID}
        with patch("handlers.admin_policy.agents_repo") as ar, \
             patch("handlers.admin_policy.approvals_repo") as apr:
            ar.get.return_value = agent_no_mode
            apr.list_by_agent.return_value = []
            r = handle_get_policy(AGENT_ID, ADMIN)
        assert json.loads(r["body"])["mode"] == "wild"


class TestSetMode:
    def test_unauthorized(self):
        r = handle_set_mode(AGENT_ID, {"mode": "readonly"}, "wrong")
        assert r["statusCode"] == 401

    def test_invalid_mode(self):
        with patch("handlers.admin_policy.agents_repo") as ar, \
             patch("handlers.admin_policy.approvals_repo"):
            ar.get.return_value = _AGENT
            r = handle_set_mode(AGENT_ID, {"mode": "superuser"}, ADMIN)
        assert r["statusCode"] == 400

    def test_missing_mode(self):
        with patch("handlers.admin_policy.agents_repo") as ar, \
             patch("handlers.admin_policy.approvals_repo"):
            ar.get.return_value = _AGENT
            r = handle_set_mode(AGENT_ID, {}, ADMIN)
        assert r["statusCode"] == 400

    def test_agent_not_found(self):
        with patch("handlers.admin_policy.agents_repo") as ar, \
             patch("handlers.admin_policy.approvals_repo"):
            ar.get.return_value = None
            r = handle_set_mode(AGENT_ID, {"mode": "readonly"}, ADMIN)
        assert r["statusCode"] == 404

    @pytest.mark.parametrize("mode", ["wild", "readonly", "approved"])
    def test_valid_modes(self, mode):
        with patch("handlers.admin_policy.agents_repo") as ar, \
             patch("handlers.admin_policy.approvals_repo"):
            ar.get.return_value = _AGENT
            r = handle_set_mode(AGENT_ID, {"mode": mode}, ADMIN)
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["mode"] == mode

    def test_calls_update_policy(self):
        with patch("handlers.admin_policy.agents_repo") as ar, \
             patch("handlers.admin_policy.approvals_repo"):
            ar.get.return_value = _AGENT
            handle_set_mode(AGENT_ID, {"mode": "readonly"}, ADMIN)
        ar.update_policy.assert_called_once_with(AGENT_ID, "readonly")
