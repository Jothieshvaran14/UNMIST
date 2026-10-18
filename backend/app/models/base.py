"""Shared model mixins and portable column types."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, String, TypeDecorator, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

# 36-character canonical UUID string. Portable across SQLite and PostgreSQL
# without requiring the pgcrypto extension.
GUID = String(36)


def uuid_str() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """DateTime that is always timezone-aware UTC in Python.

    SQLite has no native timezone support and returns naive values, while
    PostgreSQL ``timestamptz`` returns aware values. This decorator normalizes
    both to aware UTC so application code can compare timestamps reliably.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class IdMixin:
    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=uuid_str)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )


__all__ = [
    "Base",
    "GUID",
    "IdMixin",
    "TimestampMixin",
    "UTCDateTime",
    "utcnow",
    "uuid_str",
    "Any",
]
