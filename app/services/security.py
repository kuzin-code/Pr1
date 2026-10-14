"""Хеширование паролей (PBKDF2-SHA256)."""
import hashlib
import hmac
import os
import secrets


def _iterations() -> int:
    return int(os.environ.get("APP_PBKDF2_ITERATIONS", "200000"))


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _iterations())
    return f"pbkdf2${_iterations()}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(check.hex(), digest)
