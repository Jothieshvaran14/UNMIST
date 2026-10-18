"""Organization and membership services."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from ..models import Org, OrgMembership, Role, User
from .audit import record_audit

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:100] or "org"


def get_org(session: Session, org_id: str) -> Org:
    org = session.get(Org, org_id)
    if org is None:
        raise NotFoundError("Organization not found")
    return org


def create_org(
    session: Session,
    *,
    actor: User,
    name: str,
    is_institution: bool = False,
) -> Org:
    base_slug = slugify(name)
    slug = base_slug
    suffix = 1
    while session.execute(select(Org).where(Org.slug == slug)).scalar_one_or_none() is not None:
        suffix += 1
        slug = f"{base_slug}-{suffix}"
    org = Org(name=name.strip(), slug=slug, is_institution=is_institution)
    session.add(org)
    session.flush()
    session.add(OrgMembership(org_id=org.id, user_id=actor.id, role=Role.owner.value))
    record_audit(
        session,
        action="org.create",
        actor_id=actor.id,
        org_id=org.id,
        resource_type="organization",
        resource_id=org.id,
        details={"name": name, "is_institution": is_institution},
    )
    return org


def list_members(session: Session, org_id: str) -> list[tuple[OrgMembership, User]]:
    # SQLAlchemy's Row is tuple-compatible at runtime; mypy treats it as distinct.
    return list(
        session.query(OrgMembership, User)  # type: ignore[arg-type]
        .join(User, User.id == OrgMembership.user_id)
        .filter(OrgMembership.org_id == org_id)
        .order_by(OrgMembership.joined_at.asc())
        .all()
    )


def add_member(
    session: Session,
    *,
    org: Org,
    actor: User,
    email: str,
    role: str,
) -> OrgMembership:
    normalized = email.strip().lower()
    if not normalized:
        raise ValidationError("A valid member email is required")
    user = session.execute(select(User).where(User.email == normalized)).scalar_one_or_none()
    if user is None:
        # We do not create accounts on behalf of people; the invitee must
        # register first. This avoids creating accounts without consent.
        raise NotFoundError("No registered account exists for that email")
    existing = session.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == org.id, OrgMembership.user_id == user.id
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("User is already a member of this organization")

    membership = OrgMembership(org_id=org.id, user_id=user.id, role=role)
    session.add(membership)
    session.flush()
    record_audit(
        session,
        action="org.member.add",
        actor_id=actor.id,
        org_id=org.id,
        resource_type="org_membership",
        resource_id=membership.id,
        details={"member_id": user.id, "role": role},
    )
    return membership


def update_member_role(
    session: Session,
    *,
    org: Org,
    actor: User,
    user_id: str,
    role: str,
) -> OrgMembership:
    if role not in ROLE_VALUES:
        raise ValidationError(f"role must be one of {sorted(ROLE_VALUES)}")
    membership = session.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == org.id, OrgMembership.user_id == user_id
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("Membership not found")
    if membership.user_id == actor.id and role != membership.role:
        raise AuthorizationError("You cannot change your own role")
    membership.role = role
    session.flush()
    record_audit(
        session,
        action="org.member.role_change",
        actor_id=actor.id,
        org_id=org.id,
        resource_type="org_membership",
        resource_id=membership.id,
        details={"member_id": user_id, "new_role": role},
    )
    return membership


def remove_member(
    session: Session, *, org: Org, actor: User, user_id: str
) -> None:
    membership = session.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == org.id, OrgMembership.user_id == user_id
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("Membership not found")
    if membership.user_id == actor.id:
        raise AuthorizationError("You cannot remove yourself; transfer ownership first")
    session.delete(membership)
    session.flush()
    record_audit(
        session,
        action="org.member.remove",
        actor_id=actor.id,
        org_id=org.id,
        resource_type="org_membership",
        resource_id=membership.id,
        details={"member_id": user_id},
    )


ROLE_VALUES = {role.value for role in Role}
