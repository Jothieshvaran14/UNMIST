"""Authentication API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...errors import AuthenticationError, ValidationError
from ...models import Org, OrgMembership, User
from ...schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    OrgMembershipResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserProfileResponse,
    UserResponse,
)
from ...security.dependencies import get_current_user
from ...security.passwords import hash_password, verify_password
from ...services.audit import record_audit
from ...services.auth import (
    authenticate,
    issue_token_pair,
    register_user,
    revoke_all_user_tokens,
    rotate_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, session: Session = Depends(get_db)):
    user = register_user(
        session,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )
    session.commit()
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, session: Session = Depends(get_db)):
    user = authenticate(session, email=payload.email, password=payload.password)
    pair = issue_token_pair(
        session,
        user=user,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    record_audit(
        session,
        action="auth.login",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
    )
    session.commit()
    return pair


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, request: Request, session: Session = Depends(get_db)):
    pair = rotate_refresh_token(
        session,
        raw_token=payload.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    session.commit()
    return pair


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    # Revoke all tokens for this user (global logout). The presented access token
    # remains valid until it expires, which is bounded by ACCESS_TOKEN_TTL_SECONDS.
    revoked = revoke_all_user_tokens(session, user_id=current_user.id)
    record_audit(
        session,
        action="auth.logout",
        actor_id=current_user.id,
        resource_type="user",
        resource_id=current_user.id,
        details={"revoked_tokens": revoked},
    )
    session.commit()
    return None


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise AuthenticationError("Current password is incorrect")
    if payload.current_password == payload.new_password:
        raise ValidationError("New password must differ from the current password")
    current_user.password_hash = hash_password(payload.new_password)
    revoke_all_user_tokens(session, user_id=current_user.id)
    record_audit(
        session,
        action="auth.password_changed",
        actor_id=current_user.id,
        resource_type="user",
        resource_id=current_user.id,
    )
    session.commit()
    return None


@router.get("/me", response_model=UserProfileResponse)
def me(current_user: User = Depends(get_current_user), session: Session = Depends(get_db)):
    # Load org details efficiently in one query.
    rows = (
        session.query(OrgMembership, Org)
        .join(Org, Org.id == OrgMembership.org_id)
        .filter(OrgMembership.user_id == current_user.id)
        .all()
    )
    orgs = [
        OrgMembershipResponse(
            org_id=membership.org_id,
            org_name=org.name,
            org_slug=org.slug,
            role=membership.role,
            joined_at=membership.joined_at,
        )
        for membership, org in rows
    ]
    return UserProfileResponse(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        email_verified=current_user.email_verified,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
        organizations=orgs,
    )
