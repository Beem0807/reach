from shared.redact import redact

# NOTE: the values below are fake but match real provider formats (that's the
# point - they exercise the redactor's patterns). To keep secret-scanning push
# protection from flagging the literals, every provider-shaped value is assembled
# from fragments at runtime so no complete pattern appears in the source text.


def test_none_passthrough():
    assert redact(None) is None


def test_empty_passthrough():
    assert redact("") == ""


def test_plain_text_unchanged():
    assert redact("uptime\n 12:03 up 42 days") == "uptime\n 12:03 up 42 days"


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------

def test_aws_key_id_redacted():
    key = "AKIA" + "IOSFODNN7EXAMPLE"
    out = redact(f"export AWS_ACCESS_KEY_ID={key}")
    assert key not in out
    assert "[AWS_KEY_ID]" in out


def test_aws_secret_key_env_redacted():
    secret = "wJalrXUtnFEMI" + "/K7MDENG/bPxRfiCYEXAMPLEKEY"
    out = redact(f"AWS_SECRET_ACCESS_KEY={secret}")
    assert secret not in out
    assert "[AWS_SECRET]" in out


def test_aws_secret_colon_style_redacted():
    out = redact("aws_secret: supersecretvalue123")
    assert "supersecretvalue123" not in out


# ---------------------------------------------------------------------------
# PEM private keys
# ---------------------------------------------------------------------------

def test_rsa_private_key_redacted():
    body = "MIIEowIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4"
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        f"{body}\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = redact(pem)
    assert body not in out
    assert "[PRIVATE_KEY_REDACTED]" in out


def test_openssh_private_key_redacted():
    body = "b3BlbnNzaC1rZXktdjEAAAAA"
    pem = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        f"{body}\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    out = redact(pem)
    assert body not in out
    assert "[PRIVATE_KEY_REDACTED]" in out


# ---------------------------------------------------------------------------
# JWTs
# ---------------------------------------------------------------------------

def test_jwt_redacted():
    jwt = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiJ1c2VyXzEifQ." + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    out = redact(f"token={jwt}")
    assert jwt not in out
    assert "[JWT_REDACTED]" in out


# ---------------------------------------------------------------------------
# URLs with credentials
# ---------------------------------------------------------------------------

def test_postgres_url_redacted():
    out = redact("DATABASE_URL=postgresql://reach:s3cr3t@localhost:5432/reach")
    assert "s3cr3t" not in out
    assert "[CREDENTIALS_REDACTED]" in out


def test_https_basic_auth_redacted():
    out = redact("curl https://user:mypassword@api.example.com/endpoint")
    assert "mypassword" not in out
    assert "[CREDENTIALS_REDACTED]" in out


def test_mongodb_url_redacted():
    out = redact("MONGO_URL=mongodb://admin:hunter2@mongo:27017/db")
    assert "hunter2" not in out


# ---------------------------------------------------------------------------
# Bearer tokens
# ---------------------------------------------------------------------------

def test_bearer_token_redacted():
    out = redact("Authorization: Bearer eyABC123XYZ.abc.def")
    assert "eyABC123XYZ" not in out
    assert "[TOKEN_REDACTED]" in out


# ---------------------------------------------------------------------------
# Generic secret env vars
# ---------------------------------------------------------------------------

def test_password_equals_redacted():
    out = redact("PASSWORD=hunter2")
    assert "hunter2" not in out
    assert "[REDACTED]" in out


def test_api_key_colon_redacted():
    out = redact("api_key: sk-abc123def456")
    assert "sk-abc123def456" not in out


def test_token_pepper_redacted():
    out = redact("TOKEN_PEPPER=deadbeefcafebabe1234")
    assert "deadbeefcafebabe1234" not in out


def test_client_secret_redacted():
    out = redact("client_secret=supersecret")
    assert "supersecret" not in out


# ---------------------------------------------------------------------------
# GitHub / GitLab / Slack tokens
# ---------------------------------------------------------------------------

def test_github_pat_redacted():
    tok = "ghp_" + "16C7e42F292c6912E7710c838347Ae178B4a"
    out = redact(f"GITHUB_TOKEN={tok}")
    assert tok not in out
    assert "[TOKEN_REDACTED]" in out


def test_slack_bot_token_redacted():
    tok = "xoxb-" + "123456789012-abcdefghijklmnop"
    out = redact(f"SLACK_TOKEN={tok}")
    assert tok not in out


# ---------------------------------------------------------------------------
# Hex secrets
# ---------------------------------------------------------------------------

def test_hex_secret_redacted():
    # Caught by the generic secret env-var pattern (runs before the hex pattern).
    out = redact("secret=abcdef1234567890abcdef1234567890ab")
    assert "abcdef1234567890abcdef1234567890ab" not in out


def test_hex_secret_standalone_key_redacted():
    # Standalone "key:" at a word boundary is caught by the hex pattern.
    out = redact("key: abcdef1234567890abcdef1234567890ab")
    assert "abcdef1234567890abcdef1234567890ab" not in out


# ---------------------------------------------------------------------------
# Compound env var names (the \b → (?<![A-Za-z]) fix)
# ---------------------------------------------------------------------------

def test_postgres_password_compound_redacted():
    out = redact("POSTGRES_PASSWORD=mysecret")
    assert "mysecret" not in out

def test_mysql_root_password_redacted():
    out = redact("MYSQL_ROOT_PASSWORD=rootpass")
    assert "rootpass" not in out

def test_stripe_secret_key_compound_redacted():
    out = redact("STRIPE_SECRET_KEY=supersecret")
    assert "supersecret" not in out

def test_openai_api_key_compound_redacted():
    out = redact("OPENAI_API_KEY=sk-abc123")
    assert "sk-abc123" not in out

def test_non_letter_prefix_ok():
    # A dash or equals before the word is fine
    out = redact("--password=hunter2")
    assert "hunter2" not in out

def test_letter_run_not_matched():
    # "haspassword" - no separator, should NOT be redacted (would mangle SQL column names etc.)
    out = redact("haspassword=true")
    assert "haspassword=true" == out


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------

def test_google_api_key_redacted():
    # AIza + exactly 35 chars = valid key length
    key = "AIza" + "SyD-abcdefghijklmnopqrstuvwxyz01234"
    out = redact(f"key={key}")
    assert key not in out
    assert "[GOOGLE_API_KEY]" in out

def test_google_oauth_token_redacted():
    tok = "ya29." + "A0ARrdaM-longaccesstokenvalue"
    out = redact(f"token={tok}")
    assert tok not in out
    assert "[GOOGLE_OAUTH_TOKEN]" in out


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------

def test_stripe_live_key_redacted():
    key = "sk_live_" + "abcdefghijklmnop1234"
    out = redact(f"STRIPE_KEY={key}")
    assert key not in out
    assert "[STRIPE_KEY]" in out

def test_stripe_test_key_redacted():
    key = "sk_test_" + "ABCDEFGHIJKLMNOPQRSTUVWX"
    out = redact(key)
    assert key not in out

def test_stripe_restricted_key_redacted():
    key = "rk_live_" + "abcdefghijklmnopqrstuvwx"
    out = redact(key)
    assert key not in out


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

def test_openai_classic_key_redacted():
    key = "sk-" + "abcdefghijklmnopqrstuvwxyz01234567890123456789"
    out = redact(f"Authorization: Bearer {key}")
    assert "sk-" + "abcdefghijklmnopqrstuvwxyz" not in out

def test_openai_project_key_redacted():
    # Bare key so the OpenAI-specific pattern fires (not the generic api_key pattern).
    key = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz01234"
    out = redact(key)
    assert key not in out
    assert "[OPENAI_KEY]" in out


# ---------------------------------------------------------------------------
# HashiCorp Vault
# ---------------------------------------------------------------------------

def test_vault_service_token_redacted():
    tok = "hvs." + "AAAAAQabcdefghijklmnop"
    out = redact(f"VAULT_TOKEN={tok}")
    assert tok not in out
    assert "[VAULT_TOKEN]" in out

def test_vault_batch_token_redacted():
    tok = "hvb." + "AAAAAQabcdefghijklmnop"
    out = redact(f"token: {tok}")
    assert tok not in out


# ---------------------------------------------------------------------------
# npm
# ---------------------------------------------------------------------------

def test_npm_token_redacted():
    npm_tok = "npm_" + "A" * 36
    out = redact(f"NPM_TOKEN={npm_tok}")
    assert npm_tok not in out
    assert "[NPM_TOKEN]" in out


# ---------------------------------------------------------------------------
# SendGrid
# ---------------------------------------------------------------------------

def test_sendgrid_key_redacted():
    # Bare key so the SendGrid-specific pattern fires (not the generic api_key pattern).
    sg_key = "SG." + "A" * 22 + "." + "B" * 43
    out = redact(sg_key)
    assert sg_key not in out
    assert "[SENDGRID_KEY]" in out


# ---------------------------------------------------------------------------
# Realistic output samples
# ---------------------------------------------------------------------------

def test_printenv_output_redacted():
    aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
    aws_secret = "wJalrXUtnFEMI" + "/K7MDENG/bPxRfiCYEXAMPLEKEY"
    env_output = (
        "PATH=/usr/bin:/bin\n"
        "HOME=/root\n"
        f"AWS_ACCESS_KEY_ID={aws_key}\n"
        f"AWS_SECRET_ACCESS_KEY={aws_secret}\n"
        "DATABASE_URL=postgresql://app:s3cr3t@db:5432/prod\n"
        "HOSTNAME=prod-web-01\n"
    )
    out = redact(env_output)
    assert aws_key not in out
    assert aws_secret not in out
    assert "s3cr3t" not in out
    assert "PATH=/usr/bin:/bin" in out
    assert "HOSTNAME=prod-web-01" in out


def test_df_output_not_mangled():
    df = (
        "Filesystem      Size  Used Avail Use% Mounted on\n"
        "/dev/sda1        50G   18G   30G  38% /\n"
        "tmpfs           7.8G  1.2G  6.6G  16% /dev/shm\n"
    )
    assert redact(df) == df
