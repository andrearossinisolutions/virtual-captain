"""Password hashing (argon2id), input normalisation and token helpers."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from starlette.concurrency import run_in_threadpool

from server.config import ADDR_RE

PASSWORD_MIN = 10
PASSWORD_MAX = 128
EMAIL_MAX = 254

_hasher = PasswordHasher(time_cost=2, memory_cost=32 * 1024, parallelism=1)
_slots = threading.BoundedSemaphore(2)  # at most 2 hashes at once (RAM: 2 x 32 MiB)


def _encodable(text: str) -> bool:
    """False for strings with lone surrogates (cannot be UTF-8 encoded)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def normalize_password(raw) -> str | None:
    """NFKC-normalised password, or None if it violates the length rules."""
    if not isinstance(raw, str):
        return None
    pw = unicodedata.normalize("NFKC", raw)
    if not (PASSWORD_MIN <= len(pw) <= PASSWORD_MAX) or not _encodable(pw):
        return None
    return pw


def normalize_login_password(raw) -> str | None:
    """Same normalisation, only the maximum length is enforced (old passwords may differ)."""
    if not isinstance(raw, str):
        return None
    pw = unicodedata.normalize("NFKC", raw)
    if not pw or len(pw) > PASSWORD_MAX or not _encodable(pw):
        return None
    return pw


def normalize_email(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if not email or len(email) > EMAIL_MAX or not email.isascii() or not ADDR_RE.match(email):
        return None
    return email


def _hash(password: str) -> str:
    with _slots:
        return _hasher.hash(password)


def _verify(stored: str, password: str) -> bool:
    with _slots:
        try:
            return _hasher.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


async def hash_password(password: str) -> str:
    return await run_in_threadpool(_hash, password)


async def verify_password(stored: str, password: str) -> bool:
    return await run_in_threadpool(_verify, stored, password)


# computed once at import (startup), so the first unknown-account login is not slower
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


async def verify_dummy(password: str) -> bool:
    """Burns the same time as a real verification (unknown account)."""
    await verify_password(_DUMMY_HASH, password)
    return False


def is_token_shaped(token) -> bool:
    """Tokens are ASCII urlsafe strings of sane length: anything else is rejected early."""
    return (isinstance(token, str) and 16 <= len(token) <= 200 and token.isascii())


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
