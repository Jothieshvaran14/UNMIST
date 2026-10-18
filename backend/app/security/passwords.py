"""Password hashing using Argon2id (via argon2-cffi).

Argon2id is the current OWASP-recommended password-hashing algorithm. Parameters
are configurable so they can be tuned to the deployment hardware.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from ..config import Settings, get_settings


def _hasher(settings: Settings) -> PasswordHasher:
    # argon2-cffi >=21.2 defaults to Argon2id.
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost,
        parallelism=settings.argon2_parallelism,
    )


def hash_password(password: str, settings: Settings | None = None) -> str:
    if not password:
        raise ValueError("password must not be empty")
    return _hasher(settings or get_settings()).hash(password)


def verify_password(password: str, password_hash: str, settings: Settings | None = None) -> bool:
    if not password or not password_hash:
        return False
    try:
        return _hasher(settings or get_settings()).verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def needs_rehash(password_hash: str, settings: Settings | None = None) -> bool:
    """Whether the stored hash was produced with weaker parameters."""
    try:
        return _hasher(settings or get_settings()).check_needs_rehash(password_hash)
    except (VerifyMismatchError, VerificationError):
        return False
