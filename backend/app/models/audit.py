"""Audit trail model."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from ..db import Base
from .base import GUID, IdMixin, TimestampMixin


class AuditEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "audit_events"

    actor_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    org_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(GUID, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Note: the generic ``JSON`` type is used for SQLite/PostgreSQL portability.
    # A future PostgreSQL-only optimization may switch this to ``JSONB``.
