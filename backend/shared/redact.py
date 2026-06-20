"""
Best-effort redaction of common secret patterns from command output.

Applied before stdout/stderr is persisted to the database and before it is
returned to Claude via the MCP server. This is defense-in-depth - it cannot
catch every possible secret format, only structurally recognisable ones.
"""
import re
from typing import Optional

# Each entry is (compiled_pattern, replacement_string).
# Patterns are applied in order; replacements use \1 back-references where needed.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # AWS access key IDs (AKIA...)
    (
        re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
        '[AWS_KEY_ID]',
    ),
    # AWS secret keys in env-style output: AWS_SECRET_ACCESS_KEY=...
    (
        re.compile(r'(?i)(aws_secret_access_key|aws_secret)\s*[=:]\s*\S+'),
        r'\1=[AWS_SECRET]',
    ),
    # PEM private key blocks (RSA, EC, DSA, OpenSSH, generic)
    (
        re.compile(
            r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'
            r'.*?'
            r'-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
            re.DOTALL,
        ),
        '[PRIVATE_KEY_REDACTED]',
    ),
    # JWT tokens (three base64url segments starting with eyJ)
    (
        re.compile(r'\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b'),
        '[JWT_REDACTED]',
    ),
    # Credentials embedded in URLs: proto://user:pass@host
    (
        re.compile(
            r'(?i)(https?|postgres(?:ql)?|mysql|mongodb|redis|amqp)://'
            r'[^:@\s]+:[^@\s]+@'
        ),
        r'\1://[CREDENTIALS_REDACTED]@',
    ),
    # Bearer tokens in output (e.g. curl -H "Authorization: Bearer ...")
    (
        re.compile(r'(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*'),
        'Bearer [TOKEN_REDACTED]',
    ),
    # Common secret env-var names - standalone or as compound suffix (e.g. POSTGRES_PASSWORD).
    # (?<![A-Za-z]) instead of \b so that _PASSWORD= is matched but haspassword= is not.
    (
        re.compile(
            r'(?i)(?<![A-Za-z])(password|passwd|secret|api[_-]?key|auth[_-]?token|'
            r'access[_-]?token|private[_-]?key|secret[_-]?key|client[_-]?secret|'
            r'db[_-]?password|database[_-]?password|smtp[_-]?password|'
            r'token[_-]?pepper|token[_-]?secret)\s*[=:]\s*\S+'
        ),
        r'\1=[REDACTED]',
    ),
    # GitHub / GitLab / Slack tokens: ghp_..., ghs_..., xoxb-..., xoxp-..., glpat-...
    (
        re.compile(r'\b(ghp|ghs|gho|github_pat|glpat|xoxb|xoxp)[_-][A-Za-z0-9_-]{10,}\b'),
        r'\1_[TOKEN_REDACTED]',
    ),
    # Google API keys: AIza followed by 35 url-safe chars
    (
        re.compile(r'\bAIza[0-9A-Za-z\-_]{35}\b'),
        '[GOOGLE_API_KEY]',
    ),
    # Google OAuth access tokens: ya29.<token>
    (
        re.compile(r'\bya29\.[0-9A-Za-z\-_]+\b'),
        '[GOOGLE_OAUTH_TOKEN]',
    ),
    # Stripe secret/restricted keys: sk_live_, sk_test_, rk_live_
    (
        re.compile(r'\b(sk_live|sk_test|rk_live)_[A-Za-z0-9]{10,}\b'),
        r'\1_[STRIPE_KEY]',
    ),
    # OpenAI API keys: sk-... (classic) and sk-proj-... (project keys)
    (
        re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b'),
        '[OPENAI_KEY]',
    ),
    # HashiCorp Vault tokens: hvs. (service), hvb. (batch), hvr. (recovery)
    (
        re.compile(r'\bhv[sbr]\.[A-Za-z0-9_-]{10,}\b'),
        '[VAULT_TOKEN]',
    ),
    # npm tokens: npm_<36 alphanumeric chars>
    (
        re.compile(r'\bnpm_[A-Za-z0-9]{36}\b'),
        '[NPM_TOKEN]',
    ),
    # SendGrid API keys: SG.<22chars>.<43chars>
    (
        re.compile(r'\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b'),
        '[SENDGRID_KEY]',
    ),
    # Generic high-entropy hex secrets (≥32 hex chars) after a key-name context.
    # (?<![A-Za-z]) so that compound names like encryption_key: <hex> are caught too.
    (
        re.compile(r'(?i)(?<![A-Za-z])(key|token|secret|password)\s*[=:]\s*[0-9a-f]{32,}\b'),
        r'\1=[HEX_SECRET_REDACTED]',
    ),
]


def redact(text: Optional[str]) -> Optional[str]:
    """Return *text* with known secret patterns replaced by placeholder strings.

    Returns None unchanged so callers don't need to guard against None inputs.
    """
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
