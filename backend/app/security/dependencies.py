"""Authentication and tenant-authorization FastAPI dependencies.

Authorization is always enforced server-side. Object-level and tenant-level
checks live here rather than in the frontend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AuthenticationError, AuthorizationError
from ..models import OrgMembership, User
from .rbac import role_at_least
from .tokens import TokenError, decode_access_token

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class TenantContext:
    user: User
    org_id: str
    role: str


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    session: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise AuthenticationError("Authentication required")
    if credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Unsupported authentication scheme")
    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise AuthenticationError(str(exc)) from exc

    user = session.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise AuthenticationError("User account is unavailable")
    return user


class OrgRole:
    """Dependency factory enforcing a minimum role within an organization.

    Usage::

        @router.get("/orgs/{org_id}/members")
        def list_members(ctx: TenantContext = Depends(OrgRole("admin"))):
            ...
    """

    def __init__(self, minimum_role: str):
        self.minimum_role = minimum_role

    def __call__(
        self,
        org_id: str,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_db),
    ) -> TenantContext:
        membership = session.execute(
            select(OrgMembership).where(
                OrgMembership.org_id == org_id,
                OrgMembership.user_id == user.id,
            )
        ).scalar_one_or_none()

        if membership is None:
            raise AuthorizationError("You are not a member of this organization")
        if not role_at_least(membership.role, self.minimum_role):
            raise AuthorizationError(
                f"This action requires the '{self.minimum_role}' role or higher"
            )
        return TenantContext(user=user, org_id=org_id, role=membership.role)


def get_membership(session: Session, user_id: str, org_id: str) -> Optional[OrgMembership]:
    return session.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == org_id,
            OrgMembership.user_id == user_id,
        )
    ).scalar_one_or_none()
