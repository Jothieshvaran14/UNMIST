"""Retention models: spaced-repetition scheduling and review history."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import GUID, IdMixin, TimestampMixin, UTCDateTime


class RetentionStatus(str, Enum):
    never_assessed = "never_assessed"
    learning = "learning"
    demonstrated = "demonstrated"
    retained = "retained"
    at_risk = "at_risk"
    failing = "failing"
    requires_reassessment = "requires_reassessment"
    insufficient_evidence = "insufficient_evidence"


class RetentionSchedule(IdMixin, TimestampMixin, Base):
    __tablename__ = "retention_schedules"

    learner_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    concept_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("concepts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    org_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(40), default=RetentionStatus.never_assessed.value, nullable=False
    )
    ease_factor: Mapped[float] = mapped_column(default=2.5, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    repetitions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lapses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_review_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    timezone: Mapped[str] = mapped_column(String(60), default="UTC", nullable=False)

    __table_args__ = (
        UniqueConstraint("learner_id", "concept_id", "org_id", name="uq_retention"),
    )


class ReviewAttempt(IdMixin, TimestampMixin, Base):
    __tablename__ = "review_attempts"

    schedule_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("retention_schedules.id", ondelete="SET NULL"), nullable=True
    )
    learner_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    concept_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("concepts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    org_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    recalled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quality: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prompt_type: Mapped[str] = mapped_column(String(40), default="recall", nullable=False)
