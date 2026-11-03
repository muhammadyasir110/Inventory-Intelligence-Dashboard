"""
core/security.py
-----------------
Password hashing and session-token helpers (standard library only).

Passwords: PBKDF2-HMAC-SHA256 with a 16-byte random salt per user, stored as
    pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
Hashes produced by IIP <= 2.1 (unsalted-per-user SHA-256 hex) are still
accepted once so existing users can log in, and are replaced by a PBKDF2
hash on that successful login (`needs_rehash`).

Session tokens: 256-bit random values. Only SHA-256(token) is stored in the
database, so a leaked database does not reveal usable tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from core.settings import settings

_LEGACY_SALT = "IIP_ENTERPRISE_SECRET_SALT_2026"   # IIP <= 2.1, verification only
_LEGACY_RE = re.compile(r"^[0-9a-f]{64}$")
PREFIX = "pbkdf2_sha256"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str, iterations: int | None = None) -> str:
    if not isinstance(password, str) or password == "":
        raise ValueError("Password must be a non-empty string.")
    iterations = iterations or settings.pbkdf2_iterations
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PREFIX}${iterations}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not isinstance(password, str):
        return False
    if stored.startswith(PREFIX + "$"):
        try:
            _, it, salt_b64, hash_b64 = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     base64.b64decode(salt_b64), int(it))
            return hmac.compare_digest(dk, base64.b64decode(hash_b64))
        except (ValueError, TypeError):
            return False
    if _LEGACY_RE.match(stored):
        legacy = hashlib.sha256((password + _LEGACY_SALT).encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy, stored)
    return False


def needs_rehash(stored: str | None) -> bool:
    if not stored or not stored.startswith(PREFIX + "$"):
        return True
    try:
        return int(stored.split("$")[1]) < settings.pbkdf2_iterations
    except (IndexError, ValueError):
        return True


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_password_strength(password: str) -> None:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if settings.is_production and len(password) < 12:
        raise ValueError("Password must be at least 12 characters in production.")
