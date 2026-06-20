import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split('$', 2)
        h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 200_000)
        return hmac.compare_digest(h.hex(), expected)
    except Exception:
        return False


def generate_temp_password() -> str:
    """Returns a human-friendly 16-char temp password."""
    return secrets.token_urlsafe(12)
