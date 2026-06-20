import json
from unittest.mock import patch

from handlers.me import handle_me

USER = {"user_id": "user_1", "tenant_id": "tenant_1", "name": "alice", "created_at": "2026-01-01T00:00:00+00:00"}


def test_unauthorized():
    with patch("handlers.me._verify_tenant_token", return_value=None):
        r = handle_me("bad")
    assert r["statusCode"] == 401


def test_returns_user_fields():
    with patch("handlers.me._verify_tenant_token", return_value=USER):
        r = handle_me("tok_abc")
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["user_id"] == "user_1"
    assert body["tenant_id"] == "tenant_1"
    assert body["name"] == "alice"
    assert body["created_at"] == "2026-01-01T00:00:00+00:00"


def test_name_can_be_none():
    user_no_name = {**USER, "name": None}
    with patch("handlers.me._verify_tenant_token", return_value=user_no_name):
        r = handle_me("tok_abc")
    assert json.loads(r["body"])["name"] is None


def test_api_key_token_passed_to_verify():
    """tok_... tokens must reach _verify_tenant_token (which routes to _verify_api_key internally)."""
    captured = []
    def spy(tok):
        captured.append(tok)
        return USER
    with patch("handlers.me._verify_tenant_token", side_effect=spy):
        r = handle_me("tok_myapikey123")
    assert r["statusCode"] == 200
    assert captured == ["tok_myapikey123"]


