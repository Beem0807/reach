from unittest.mock import patch

from shared.auth import _bearer, _hmac_token


def test_hmac_token_is_deterministic():
    assert _hmac_token("test-token") == _hmac_token("test-token")


def test_hmac_token_different_inputs_produce_different_hashes():
    assert _hmac_token("token-a") != _hmac_token("token-b")


def test_hmac_token_returns_hex_string():
    result = _hmac_token("anything")
    assert isinstance(result, str)
    int(result, 16)  # raises ValueError if not hex


def test_hmac_token_length():
    # SHA-256 hex digest is always 64 chars
    assert len(_hmac_token("x")) == 64


def test_bearer_extracts_token():
    event = {"headers": {"authorization": "Bearer tok_abc123"}}
    assert _bearer(event) == "tok_abc123"


def test_bearer_case_insensitive_scheme():
    event = {"headers": {"authorization": "bearer tok_abc123"}}
    assert _bearer(event) == "tok_abc123"


def test_bearer_strips_whitespace():
    event = {"headers": {"authorization": "Bearer   tok_abc123  "}}
    assert _bearer(event) == "tok_abc123"


def test_bearer_returns_none_if_no_auth_header():
    assert _bearer({}) is None
    assert _bearer({"headers": {}}) is None


def test_bearer_returns_none_for_non_bearer_scheme():
    assert _bearer({"headers": {"authorization": "Basic abc"}}) is None


def test_bearer_returns_none_for_empty_header():
    assert _bearer({"headers": {"authorization": ""}}) is None


def test_verify_tenant_token_found():
    from shared.auth import _verify_tenant_token
    user = {"user_id": "u1", "tenant_id": "t1"}
    with patch("shared.store.users_repo") as mock:
        mock.get_by_hash.return_value = user
        result = _verify_tenant_token("tok_abc")
    assert result == user


def test_verify_tenant_token_not_found():
    from shared.auth import _verify_tenant_token
    with patch("shared.store.users_repo") as mock:
        mock.get_by_hash.return_value = None
        result = _verify_tenant_token("tok_bad")
    assert result is None


def test_verify_agent_token_valid():
    from shared.auth import _verify_agent_token
    raw = "agent_secret"
    agent = {"agent_id": "agent_a", "agent_token_hash": _hmac_token(raw)}
    with patch("shared.store.agents_repo") as mock:
        mock.get.return_value = agent
        result = _verify_agent_token(raw, "agent_a")
    assert result == agent


def test_verify_agent_token_wrong_hash():
    from shared.auth import _verify_agent_token
    agent = {"agent_id": "agent_a", "agent_token_hash": _hmac_token("correct")}
    with patch("shared.store.agents_repo") as mock:
        mock.get.return_value = agent
        result = _verify_agent_token("wrong", "agent_a")
    assert result is None


def test_verify_agent_token_agent_not_found():
    from shared.auth import _verify_agent_token
    with patch("shared.store.agents_repo") as mock:
        mock.get.return_value = None
        result = _verify_agent_token("tok", "agent_a")
    assert result is None
