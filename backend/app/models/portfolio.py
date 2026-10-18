"""Portfolio models: opt-in public projections of private learner records."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import GUID, IdMixin, TimestampMixin, UTCDateTime


class PortfolioSetting(IdMixin, TimestampMixin, Base):
    __tablename__ = "portfolio_settings"

    user_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    headline: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # When True, only assessed evidence may be published; self-reported skills
    # are excluded from the public projection.
    show_assessed_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class PortfolioItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "portfolio_items"

    user_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    concept_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("concepts.id", ondelete="SET NULL"), nullable=True
    )
    item_type: Mapped[str] = mapped_column(String(40), default="concept", nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    evidence_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_self_reported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), default="private", nullable=False)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
