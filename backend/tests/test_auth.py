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
    tenant = {"tenant_id": "t1", "status": "ACTIVE"}
    payload = {"sub": "u1", "tenant_id": "t1"}
    with patch("shared.auth.verify_tenant_token" if False else "shared.tenant_auth.verify_tenant_token", return_value=payload), \
         patch("shared.store.users_repo") as umock, \
         patch("shared.store.tenants_repo") as tmock:
        umock.get.return_value = user
        tmock.get.return_value = tenant
        result = _verify_tenant_token("jwt_abc")
    assert result == user


def test_verify_tenant_token_disabled_tenant_blocked():
    from shared.auth import _verify_tenant_token
    user = {"user_id": "u1", "tenant_id": "t1"}
    tenant = {"tenant_id": "t1", "status": "DISABLED"}
    payload = {"sub": "u1", "tenant_id": "t1"}
    with patch("shared.tenant_auth.verify_tenant_token", return_value=payload), \
         patch("shared.store.users_repo") as umock, \
         patch("shared.store.tenants_repo") as tmock:
        umock.get.return_value = user
        tmock.get.return_value = tenant
        result = _verify_tenant_token("jwt_abc")
    assert result is None


def test_verify_tenant_token_invalid_jwt():
    from shared.auth import _verify_tenant_token
    with patch("shared.tenant_auth.verify_tenant_token", return_value=None):
        result = _verify_tenant_token("not_a_jwt")
    assert result is None


def test_verify_tenant_token_user_not_found():
    from shared.auth import _verify_tenant_token
    payload = {"sub": "u1", "tenant_id": "t1"}
    with patch("shared.tenant_auth.verify_tenant_token", return_value=payload), \
         patch("shared.store.users_repo") as umock, \
         patch("shared.store.tenants_repo"):
        umock.get.return_value = None
        result = _verify_tenant_token("jwt_abc")
    assert result is None


def test_verify_agent_token_valid():
    # Credential-only: the agent is resolved by hashing the bearer token and
    # looking it up - no agent_id is supplied.
    from shared.auth import _verify_agent_token
    raw = "agent_secret"
    agent = {"agent_id": "agent_a", "agent_token_hash": _hmac_token(raw)}
    with patch("shared.store.agents_repo") as mock:
        mock.get_by_agent_token_hash.return_value = agent
        result = _verify_agent_token(raw)
    assert result == agent
    mock.get_by_agent_token_hash.assert_called_once_with(_hmac_token(raw))


def test_verify_agent_token_wrong_hash():
    from shared.auth import _verify_agent_token
    with patch("shared.store.agents_repo") as mock:
        # No agent has this token hash -> lookup returns nothing.
        mock.get_by_agent_token_hash.return_value = None
        result = _verify_agent_token("wrong")
    assert result is None


def test_verify_agent_token_agent_not_found():
    from shared.auth import _verify_agent_token
    with patch("shared.store.agents_repo") as mock:
        mock.get_by_agent_token_hash.return_value = None
        result = _verify_agent_token("tok")
    assert result is None


# ---------------------------------------------------------------------------
# _verify_api_key
# ---------------------------------------------------------------------------

def test_verify_api_key_valid():
    from shared.auth import _verify_api_key
    raw = "tok_secret"
    stored = {"token_id": "tkid_1", "user_id": "u1", "tenant_id": "t1", "status": "ACTIVE"}
    user = {"user_id": "u1", "tenant_id": "t1"}
    tenant = {"tenant_id": "t1", "status": "ACTIVE"}
    with patch("shared.store.api_tokens_repo") as atr, \
         patch("shared.store.users_repo") as ur, \
         patch("shared.store.tenants_repo") as tr:
        atr.get_by_hash.return_value = stored
        atr.touch.return_value = None
        ur.get.return_value = user
        tr.get.return_value = tenant
        result = _verify_api_key(raw)
    assert result == user
    atr.touch.assert_called_once_with("tkid_1", atr.touch.call_args[0][1])


def test_verify_api_key_revoked_returns_none():
    from shared.auth import _verify_api_key
    stored = {"token_id": "tkid_1", "user_id": "u1", "tenant_id": "t1", "status": "REVOKED"}
    with patch("shared.store.api_tokens_repo") as atr:
        atr.get_by_hash.return_value = stored
        result = _verify_api_key("tok_secret")
    assert result is None


def test_verify_api_key_not_found_returns_none():
    from shared.auth import _verify_api_key
    with patch("shared.store.api_tokens_repo") as atr:
        atr.get_by_hash.return_value = None
        result = _verify_api_key("tok_unknown")
    assert result is None


def test_verify_api_key_disabled_tenant_returns_none():
    from shared.auth import _verify_api_key
    stored = {"token_id": "tkid_1", "user_id": "u1", "tenant_id": "t1", "status": "ACTIVE"}
    user = {"user_id": "u1", "tenant_id": "t1"}
    tenant = {"tenant_id": "t1", "status": "DISABLED"}
    with patch("shared.store.api_tokens_repo") as atr, \
         patch("shared.store.users_repo") as ur, \
         patch("shared.store.tenants_repo") as tr:
        atr.get_by_hash.return_value = stored
        ur.get.return_value = user
        tr.get.return_value = tenant
        result = _verify_api_key("tok_secret")
    assert result is None


def test_verify_api_key_user_not_found_returns_none():
    from shared.auth import _verify_api_key
    stored = {"token_id": "tkid_1", "user_id": "u1", "tenant_id": "t1", "status": "ACTIVE"}
    with patch("shared.store.api_tokens_repo") as atr, \
         patch("shared.store.users_repo") as ur, \
         patch("shared.store.tenants_repo"):
        atr.get_by_hash.return_value = stored
        ur.get.return_value = None
        result = _verify_api_key("tok_secret")
    assert result is None


# _verify_tenant_token falls through to api key when JWT fails

def test_verify_tenant_token_falls_through_to_api_key():
    from shared.auth import _verify_tenant_token
    user = {"user_id": "u1", "tenant_id": "t1"}
    with patch("shared.tenant_auth.verify_tenant_token", return_value=None), \
         patch("shared.auth._verify_api_key", return_value=user) as mock_api_key:
        result = _verify_tenant_token("tok_abc123")
    assert result == user
    mock_api_key.assert_called_once_with("tok_abc123")


def test_verify_tenant_token_non_tok_prefix_not_tried_as_api_key():
    from shared.auth import _verify_tenant_token
    with patch("shared.tenant_auth.verify_tenant_token", return_value=None), \
         patch("shared.auth._verify_api_key") as mock_api_key:
        result = _verify_tenant_token("notavalidtoken")
    assert result is None
    mock_api_key.assert_not_called()
