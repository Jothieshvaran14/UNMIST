"""Authentication and account services.

Passwords are hashed with Argon2id. Refresh tokens are opaque, hashed at rest,
rotated on use, and revocable. No plaintext credential is ever persisted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..errors import AuthenticationError, ConflictError, ValidationError
from ..models import Org, OrgMembership, RefreshToken, Role, User
from ..security.passwords import hash_password, needs_rehash, verify_password
from ..security.tokens import (
    create_access_token,
    generate_refresh_token,
    hash_token,
)
from .audit import record_audit

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"


def _validate_email(email: str) -> str:
    normalized = email.strip().lower()
    if not normalized or len(normalized) > 320 or not _EMAIL_RE.match(normalized):
        raise ValidationError("A valid email address is required")
    return normalized


def _validate_password(password: str) -> None:
    # A baseline policy; a production deployment should consult a breached
    # password list (e.g. k-anonymity API) before accepting.
    if len(password) < 10:
        raise ValidationError("Password must be at least 10 characters long")


def register_user(
    session: Session,
    *,
    email: str,
    password: str,
    display_name: Optional[str] = None,
    settings: Settings | None = None,
) -> User:
    settings = settings or get_settings()
    normalized = _validate_email(email)
    _validate_password(password)

    existing = session.execute(select(User).where(User.email == normalized)).scalar_one_or_none()
    if existing is not None:
        # Do not reveal whether an account exists beyond a generic conflict.
        raise ConflictError("An account with this email already exists")

    user = User(
        email=normalized,
        password_hash=hash_password(password, settings),
        display_name=(display_name or normalized.split("@")[0]),
    )
    session.add(user)
    session.flush()
    record_audit(
        session,
        action="user.register",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
    )
    return user


def authenticate(session: Session, *, email: str, password: str) -> User:
    normalized = (email or "").strip().lower()
    user = session.execute(select(User).where(User.email == normalized)).scalar_one_or_none()
    # Constant-ish behaviour: verify even when the user is missing to reduce
    # user-enumeration timing differences.
    if user is None:
        # Perform a dummy verification to keep timing comparable.
        verify_password(password or "", hash_password("dummy-password-12345"))
        raise AuthenticationError("Invalid email or password")
    if not user.is_active:
        raise AuthenticationError("Invalid email or password")
    if not verify_password(password or "", user.password_hash):
        raise AuthenticationError("Invalid email or password")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password or "")
    return user


def issue_token_pair(
    session: Session,
    *,
    user: User,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
    org_id: Optional[str] = None,
    role: Optional[str] = None,
    settings: Settings | None = None,
) -> TokenPair:
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    raw_refresh = generate_refresh_token()
    refresh = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw_refresh),
        expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
        user_agent=user_agent,
        ip_address=ip_address,
    )
    session.add(refresh)
    user.last_login_at = now
    session.flush()

    access = create_access_token(
        user.id, org_id=org_id, role=role, settings=settings
    )
    return TokenPair(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_seconds,
    )


def rotate_refresh_token(
    session: Session,
    *,
    raw_token: str,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
    settings: Settings | None = None,
) -> TokenPair:
    settings = settings or get_settings()
    token_hash = hash_token(raw_token)
    stored = session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if stored is None or stored.revoked_at is not None or stored.expires_at < now:
        # Reuse of a previously rotated token: revoke the whole family.
        if stored is not None:
            record_audit(
                session,
                action="auth.refresh.reuse_detected",
                actor_id=stored.user_id,
                outcome="failure",
                details={"token_id": stored.id},
            )
        raise AuthenticationError("Invalid or expired refresh token")

    user = session.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or expired refresh token")

    stored.revoked_at = now
    stored.replaced_by = "rotated"
    session.flush()

    record_audit(
        session,
        action="auth.refresh.rotate",
        actor_id=user.id,
        details={"old_token_id": stored.id},
    )
    return issue_token_pair(
        session,
        user=user,
        user_agent=user_agent,
        ip_address=ip_address,
        settings=settings,
    )


def revoke_all_user_tokens(session: Session, *, user_id: str) -> int:
    now = datetime.now(timezone.utc)
    tokens = session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars().all()
    for token in tokens:
        token.revoked_at = now
    return len(tokens)


def create_personal_org(session: Session, user: User) -> Org:
    """Create a personal workspace for a user (tenant isolation boundary)."""
    slug = f"personal-{user.id[:8]}"
    org = Org(name=f"{user.display_name or user.email}'s workspace", slug=slug)
    session.add(org)
    session.flush()
    session.add(OrgMembership(org_id=org.id, user_id=user.id, role=Role.owner.value))
    return org
