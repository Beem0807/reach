import hashlib
import hmac
import secrets

# scrypt: a memory-hard KDF (resists GPU/ASIC brute force, which PBKDF2 does not). Provided by the
# stdlib via OpenSSL, so there's no third-party dependency - the Lambda package stays dependency-free.
# Parameters are embedded in the stored hash (scrypt$N$r$p$salt$dk) so they can be tuned later without
# invalidating existing hashes. N=2**16 x r=8 => ~64 MiB of working memory per hash; p=1.
_N = 2 ** 16
_R = 8
_P = 1
_DKLEN = 32
_MAXMEM = 128 * 1024 * 1024  # ceiling so OpenSSL permits scrypt's ~64 MiB working set (default is lower)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, expected_hex = stored.split('$', 5)
        if scheme != 'scrypt':
            return False
        expected = bytes.fromhex(expected_hex)
        dk = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(expected), maxmem=_MAXMEM,
        )
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def generate_temp_password() -> str:
    """Returns a human-friendly 16-char temp password."""
    return secrets.token_urlsafe(12)
