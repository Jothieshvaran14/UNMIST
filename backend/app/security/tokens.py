"""Token creation, verification, and hashing.

Access tokens are short-lived signed JWTs (HS256). Refresh tokens are opaque
random values; only their SHA-256 hash is persisted, enabling revocation and
rotation without storing the credential itself.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt

from ..config import Settings, get_settings

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def create_access_token(
    subject: str,
    *,
    org_id: Optional[str] = None,
    role: Optional[str] = None,
    extra_claims: Optional[dict[str, Any]] = None,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
        "aud": settings.token_audience,
        "typ": ACCESS_TOKEN_TYPE,
        "jti": secrets.token_hex(16),
    }
    if org_id:
        claims["org_id"] = org_id
    if role:
        claims["role"] = role
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, settings.secret_key, algorithm="HS256")


def decode_access_token(
    token: str, settings: Settings | None = None
) -> dict[str, Any]:
    """Decode and validate an access token.

    Raises ``TokenError`` for any invalid, expired, or mis-typed token.
    """
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            audience=settings.token_audience,
        )
    except JWTError as exc:
        raise TokenError("Invalid or expired access token") from exc
    if claims.get("typ") != ACCESS_TOKEN_TYPE:
        raise TokenError("Token is not an access token")
    if not claims.get("sub"):
        raise TokenError("Token has no subject")
    return claims


class TokenError(Exception):
    """Raised when a token cannot be trusted."""
