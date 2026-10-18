"""Organization API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import User
from ...schemas.org import (
    InviteRequest,
    MembershipResponse,
    OrgCreateRequest,
    OrgResponse,
)
from ...security.dependencies import OrgRole, TenantContext, get_current_user
from ...services import orgs as org_service

router = APIRouter(prefix="/orgs", tags=["organizations"])


@router.post("", response_model=OrgResponse, status_code=status.HTTP_201_CREATED)
def create_org(
    payload: OrgCreateRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    org = org_service.create_org(
        session, actor=actor, name=payload.name, is_institution=payload.is_institution
    )
    session.commit()
    return org


@router.get("/{org_id}", response_model=OrgResponse)
def get_org(
    org_id: str,
    ctx: TenantContext = Depends(OrgRole("member")),
    session: Session = Depends(get_db),
):
    return org_service.get_org(session, org_id)


@router.get("/{org_id}/members", response_model=list[MembershipResponse])
def list_members(
    org_id: str,
    ctx: TenantContext = Depends(OrgRole("member")),
    session: Session = Depends(get_db),
):
    rows = org_service.list_members(session, org_id)
    return [
        MembershipResponse(
            user_id=membership.user_id,
            email=user.email,
            display_name=user.display_name,
            role=membership.role,
            joined_at=membership.joined_at,
        )
        for membership, user in rows
    ]


@router.post(
    "/{org_id}/members",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_member(
    org_id: str,
    payload: InviteRequest,
    ctx: TenantContext = Depends(OrgRole("admin")),
    session: Session = Depends(get_db),
):
    org = org_service.get_org(session, org_id)
    membership = org_service.add_member(
        session, org=org, actor=ctx.user, email=payload.email, role=payload.role
    )
    session.commit()
    user = session.get(User, membership.user_id)
    assert user is not None, "membership user must exist"
    return MembershipResponse(
        user_id=membership.user_id,
        email=user.email,
        display_name=user.display_name,
        role=membership.role,
        joined_at=membership.joined_at,
    )


@router.patch("/{org_id}/members/{user_id}", response_model=MembershipResponse)
def update_member_role(
    org_id: str,
    user_id: str,
    payload: InviteRequest,
    ctx: TenantContext = Depends(OrgRole("admin")),
    session: Session = Depends(get_db),
):
    org = org_service.get_org(session, org_id)
    membership = org_service.update_member_role(
        session, org=org, actor=ctx.user, user_id=user_id, role=payload.role
    )
    session.commit()
    user = session.get(User, membership.user_id)
    assert user is not None, "membership user must exist"
    return MembershipResponse(
        user_id=membership.user_id,
        email=user.email,
        display_name=user.display_name,
        role=membership.role,
        joined_at=membership.joined_at,
    )


@router.delete("/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    org_id: str,
    user_id: str,
    ctx: TenantContext = Depends(OrgRole("admin")),
    session: Session = Depends(get_db),
):
    org = org_service.get_org(session, org_id)
    org_service.remove_member(session, org=org, actor=ctx.user, user_id=user_id)
    session.commit()
    return None
