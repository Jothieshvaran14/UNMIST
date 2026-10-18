"""Tenancy models: organizations and memberships."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import GUID, IdMixin, TimestampMixin, UTCDateTime, utcnow


class Role(str, Enum):
    owner = "owner"
    admin = "admin"
    member = "member"
    faculty = "faculty"
    student = "student"


class Org(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    is_institution: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_retention_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class OrgMembership(IdMixin, Base):
    __tablename__ = "org_memberships"

    org_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(30), default=Role.member.value, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_membership_org_user"),
    )
