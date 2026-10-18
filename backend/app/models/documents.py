"""Document-processing models: pages, OCR regions, and human review.

Coordinate convention
---------------------
All bounding boxes are integer pixels in the **original image** coordinate
space (origin at the top-left, x growing right, y growing down). ``bbox_left``
and ``bbox_top`` mark the top-left corner of the region; ``bbox_width`` and
``bbox_height`` its size. Preprocessing may build a scaled working image, but
the pipeline maps boxes back to original-image pixels before persisting them,
so reviewers always see geometry that lines up with the stored original.
Box coordinates are never mixed across spaces.

Confidence semantics
--------------------
``OcrRegion.confidence`` is the provider's own score normalized to ``[0, 1]``,
or ``NULL`` when the provider reported no confidence. It is a *model* score:
it is not calibrated, it is not a correctness guarantee, and it never implies
that a human has verified the text. Human corrections live in ``OcrReview`` and
never overwrite the original prediction (``raw_text``/``text``).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from ..db import Base
from .base import GUID, IdMixin, TimestampMixin, UTCDateTime, utcnow


class DocumentProcessingState(str, Enum):
    """Lifecycle of an uploaded document through the OCR pipeline.

    A document is only ``completed`` when every page actually finished real
    processing; partial work never reaches ``completed``.
    """

    uploaded = "uploaded"
    queued = "queued"
    preprocessing = "preprocessing"
    ocr_processing = "ocr_processing"
    review_required = "review_required"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    deletion_pending = "deletion_pending"
    deleted = "deleted"


class DocumentErrorCategory(str, Enum):
    """Bounded, non-sensitive failure categories stored on the document.

    Only these coarse categories are persisted; raw exception text never is.
    """

    validation = "validation"
    storage = "storage"
    preprocessing = "preprocessing"
    ocr_unavailable = "ocr_unavailable"
    ocr_timeout = "ocr_timeout"
    ocr_failed = "ocr_failed"
    persistence = "persistence"
    internal = "internal"


class PageStatus(str, Enum):
    pending = "pending"
    processed = "processed"
    failed = "failed"


class RegionType(str, Enum):
    text_line = "text_line"
    word = "word"
    paragraph = "paragraph"


class ReviewStatus(str, Enum):
    """Review status of a single recognized region."""

    pending = "pending"
    corrected = "corrected"
    confirmed = "confirmed"


class DocumentPage(IdMixin, TimestampMixin, Base):
    """One page of an uploaded document, with its rendered geometry.

    ``width_px``/``height_px`` describe the **original** page image as stored,
    which is the coordinate space used by ``OcrRegion`` boxes.
    """

    __tablename__ = "document_pages"

    document_id: Mapped[str] = mapped_column(
        GUID,
        ForeignKey("source_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    width_px: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height_px: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Private object-store key for the preprocessed/derived page image, if one
    # was materialized. NULL when preprocessing stayed in memory only.
    processed_storage_key: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(30), default=PageStatus.pending.value, nullable=False
    )
    # Scale factors applied to produce the working image (w.r.t. the original),
    # so coordinate mapping back to original pixels is auditable.
    working_scale: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    failure_category: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_page_document_number"),
        CheckConstraint("page_number >= 1", name="ck_page_number_positive"),
        CheckConstraint(
            "(width_px IS NULL OR width_px > 0) AND "
            "(height_px IS NULL OR height_px > 0)",
            name="ck_page_dimensions_positive",
        ),
    )


class OcrRegion(IdMixin, TimestampMixin, Base):
    """A recognized region on a page, in original-image pixel coordinates."""

    __tablename__ = "ocr_regions"

    document_id: Mapped[str] = mapped_column(
        GUID,
        ForeignKey("source_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    page_id: Mapped[str] = mapped_column(
        GUID,
        ForeignKey("document_pages.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    region_type: Mapped[str] = mapped_column(
        String(40), default=RegionType.text_line.value, nullable=False
    )
    reading_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bbox_left: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_top: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_width: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_height: Mapped[int] = mapped_column(Integer, nullable=False)
    # Raw provider output, verbatim; never edited by reviewers.
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Normalized provider output (whitespace/newline normalization only).
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Provider score in [0,1], or NULL when the provider reported none.
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    processing_version: Mapped[str] = mapped_column(String(40), nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(30), default=ReviewStatus.pending.value, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "bbox_width >= 0 AND bbox_height >= 0", name="ck_region_bbox_nonneg"
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_region_confidence_unit_interval",
        ),
    )


class OcrReview(IdMixin, Base):
    """Append-only human correction or confirmation for a region.

    Corrections never overwrite the model's prediction: ``previous_text`` keeps
    the value that was live before this revision, ``corrected_text`` is the new
    value (``NULL`` for a confirmation that changes nothing), and the region's
    ``raw_text``/``text`` remain untouched.
    """

    __tablename__ = "ocr_reviews"

    region_id: Mapped[str] = mapped_column(
        GUID,
        ForeignKey("ocr_regions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corrected_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    # Kept as an explicit review timestamp rather than TimestampMixin: reviews
    # are append-only, so there is no "updated" moment to record.
    reviewer_id: Mapped[Optional[str]] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("region_id", "revision", name="uq_region_revision"),
        CheckConstraint("revision >= 1", name="ck_review_revision_positive"),
    )
