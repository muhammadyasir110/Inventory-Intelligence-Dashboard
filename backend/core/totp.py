"""
core/totp.py
-------------
Time-based one-time passwords (RFC 6238, HMAC-SHA1, 30-second steps, 6 digits),
implemented with the standard library and compatible with common authenticator
apps (Google Authenticator, Microsoft Authenticator, Authy, ...).

Verification accepts the current step and one step on either side (clock
drift) and rejects a step that was already used (replay protection is done by
the caller, which stores the last accepted step). Recovery codes are random,
shown once, and stored only as SHA-256 digests.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP_S = 30
DIGITS = 6
WINDOW = 1


def new_secret() -> str:
    """160-bit random secret, base32 without padding (RFC 4226 recommends >= 128 bits)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    s = secret_b32.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(key: bytes, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226 HOTP value for one counter."""
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def current_step(now: Optional[float] = None) -> int:
    return int((time.time() if now is None else now) // STEP_S)


def code_at(secret_b32: str, now: Optional[float] = None) -> str:
    return hotp(_key(secret_b32), current_step(now))


def verify(secret_b32: str, code: str, last_step: Optional[int] = None,
           now: Optional[float] = None) -> Optional[int]:
    """Return the matched time step, or None. Steps <= last_step are rejected (replay)."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    key = _key(secret_b32)
    t = current_step(now)
    for step in range(t - WINDOW, t + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(hotp(key, step), code):
            return step
    return None


def provisioning_uri(secret_b32: str, account: str, issuer: str = "Inventory Intelligence") -> str:
    """otpauth:// URI that authenticator apps import (also as a QR code)."""
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret_b32}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP_S}")


def new_recovery_codes(n: int = 10) -> list[str]:
    """Human-friendly single-use codes, e.g. 'k7m2-q9xd'."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" +
            "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(n)]


def digest(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace(" ", "").encode()).hexdigest()
