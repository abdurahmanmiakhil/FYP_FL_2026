"""Passwords (argon2id), JWTs, CSRF tokens, TOTP secrets (AES-GCM encrypted at rest)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

# OWASP-recommended argon2id parameters (m=19 MiB, t=2, p=1)
_ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_DUMMY_HASH = _ph.hash("timing-equaliser-not-a-password")

ALGORITHM = "HS256"
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, pw_hash: str | None) -> bool:
    """Constant-time-ish: verifies against a dummy hash when the user does not exist."""
    try:
        return _ph.verify(pw_hash or _DUMMY_HASH, pw) and pw_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(pw_hash: str) -> bool:
    return _ph.check_needs_rehash(pw_hash)


COMMON_PASSWORDS = {"password", "passw0rd", "123456", "qwerty", "letmein", "welcome", "admin", "gleasonai"}


def password_problems(pw: str, email: str = "") -> list[str]:
    """ASVS 2.1: length >= 12, not common, not containing the account name, mixed character classes."""
    s = get_settings()
    problems = []
    if len(pw) < s.PASSWORD_MIN_LENGTH:
        problems.append(f"at least {s.PASSWORD_MIN_LENGTH} characters")
    if len(pw) > 128:
        problems.append("at most 128 characters")
    classes = sum(bool(re.search(p, pw)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        problems.append("at least three of: lower case, upper case, digit, symbol")
    local = email.split("@")[0].lower()
    if local and len(local) >= 3 and local in pw.lower():
        problems.append("must not contain your email name")
    if pw.lower().strip("0123456789!@#$%^&*") in COMMON_PASSWORDS:
        problems.append("too common")
    return problems


def _key() -> bytes:
    return get_settings().SECRET_KEY.get_secret_value().encode()


def create_token(kind: str, sub: str, sid: str, minutes: float, **extra: Any) -> tuple[str, str]:
    now = datetime.now(UTC)
    jti = secrets.token_hex(16)
    payload = {
        "typ": kind,
        "sub": sub,
        "sid": sid,
        "jti": jti,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=minutes),
        **extra,
    }
    return jwt.encode(payload, _key(), algorithm=ALGORITHM), jti


def decode_token(token: str, kind: str) -> dict[str, Any]:
    data = jwt.decode(token, _key(), algorithms=[ALGORITHM], options={"require": ["exp", "sub", "sid", "typ"]})
    if data.get("typ") != kind:
        raise jwt.InvalidTokenError("wrong token type")
    return data


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(cookie: str | None, header: str | None) -> bool:
    return bool(cookie and header and hmac.compare_digest(cookie, header))


# --- TOTP (admins, optional). Secret encrypted with AES-256-GCM, key derived from SECRET_KEY.


def _aes() -> AESGCM:
    return AESGCM(hashlib.sha256(b"totp:" + _key()).digest())


def encrypt_secret(plain: str) -> str:
    nonce = os.urandom(12)
    return base64.urlsafe_b64encode(nonce + _aes().encrypt(nonce, plain.encode(), None)).decode()


def decrypt_secret(enc: str) -> str:
    raw = base64.urlsafe_b64decode(enc.encode())
    return _aes().decrypt(raw[:12], raw[12:], None).decode()


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="GleasonAI")


def verify_totp(secret: str, code: str) -> bool:
    return bool(code) and pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
