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
    Index,
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

    ``preprocessed`` means page preparation finished (pages rasterized,
    normalized, persisted) but **no OCR has run yet**. It is deliberately
    distinct from ``ocr_processing`` so a prepared-but-unrecognized document is
    never mistaken for one whose text has been recognized.
    """

    uploaded = "uploaded"
    queued = "queued"
    preprocessing = "preprocessing"
    preprocessed = "preprocessed"
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


class SegmentType(str, Enum):
    """Structural classification of one document segment (Checkpoint E).

    These describe *inferred structure*, not verified content: an ``answer``
    segment is a candidate whose text was observed on the page, never a
    judgement that the answer is correct. ``unclassified`` exists so content the
    baseline rules cannot place is represented explicitly rather than dropped.
    """

    question = "question"
    subpart = "subpart"
    answer = "answer"
    page_header = "page_header"
    page_footer = "page_footer"
    page_number = "page_number"
    diagram = "diagram"
    equation = "equation"
    table = "table"
    unclassified = "unclassified"


class SegmentUncertainty(str, Enum):
    """Categorical uncertainty for a segmentation decision.

    Deliberately **categorical, not numeric**: the baseline rules cannot
    produce a calibrated probability, so inventing a score would be misleading.
    Each value documents what a reviewer should look at:

    * ``clear`` — strong structural evidence (explicit numbering/marker).
    * ``ambiguous_boundary`` — where this segment ends and the next begins is
      uncertain (e.g. text that merely sits below a question).
    * ``unclassified`` — no recognizable structure was assigned.
    * ``suspected_continuation`` — the segment spans a page join; the
      continuation is an inference and should be confirmed by a human.
    * ``conflicting_numbering`` — numbering is duplicated or out of order.
    * ``missing_ocr`` — expected text evidence is absent or empty.
    """

    clear = "clear"
    ambiguous_boundary = "ambiguous_boundary"
    unclassified = "unclassified"
    suspected_continuation = "suspected_continuation"
    conflicting_numbering = "conflicting_numbering"
    missing_ocr = "missing_ocr"


class SegmentationStatus(str, Enum):
    """Lifecycle of document segmentation, tracked independently of
    ``DocumentProcessingState``.

    Segmentation is a downstream stage over already-recognized OCR regions and
    deliberately does **not** mutate the OCR state machine: ``processing_state``
    keeps describing preprocessing/OCR, while this column says whether
    structure extraction has run. Values:

    * ``not_run`` — never requested.
    * ``scheduled`` — a ``document.segment`` job exists but has not started.
    * ``processing`` — a worker is deriving/persisting segments.
    * ``completed`` — every input region was placed into a segment.
    * ``partial`` — persistence stopped partway; results are incomplete and
      must never be reported as complete success.
    * ``failed`` — the run failed; see ``segmentation_error_*``.
    * ``cancelled`` — stopped because the document was cancelled/deleted.
    """

    not_run = "not_run"
    scheduled = "scheduled"
    processing = "processing"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


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
    # SHA-256 of the persisted working-image artifact. Part of the idempotency
    # contract: a redelivered job recomputes this and skips work already done.
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Which preprocessing pipeline/version produced this page, so artifacts can
    # be reprocessed when the pipeline changes.
    preprocessing_version: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    failure_category: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # Free-form, non-sensitive details (processed dimensions, warnings, backend
    # used). Never stores exception internals or file contents.
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
    """A recognized region on a page, in original-image pixel coordinates.

    ``region_uid`` is a deterministic identity for a region *within a page*:
    it is derived from ``(provider, model_id, processing_version, index)``, not
    from the pixels, so a redelivered or retried OCR job recomputes the same
    uid, finds the row already present, and updates it in place instead of
    inserting a duplicate. It is NULL only for rows written before this column
    existed. Combined with a unique ``(page_id, region_uid)`` constraint this
    is the idempotency contract for OCR persistence.
    """

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
    # Deterministic identity for this region within its page (see class doc).
    region_uid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
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
        # Region identity within a page is unique: OCR persistence is idempotent.
        UniqueConstraint(
            "page_id", "region_uid", name="uq_region_page_uid"
        ),
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


class DocumentSegment(IdMixin, TimestampMixin, Base):
    """One structural segment (question / answer / subpart / furniture / …).

    Checkpoint E turns persisted OCR regions into *reviewable structure*. Four
    concepts stay strictly separate:

    1. **OCR evidence** — ``OcrRegion`` rows, never mutated by segmentation.
    2. **Segmentation** — this row: an *inference* about structure and
       boundaries, with a categorical ``uncertainty``.
    3. **Review** — ``SegmentReview`` / ``review_status``: human confirmation.
    4. **Assessment** — out of scope; nothing here says an answer is correct.

    Identity and idempotency
    ------------------------
    ``segment_uid`` is deterministic: it is derived from the **first
    contributing region id + segment type**, never from recognized text, page
    images, or the algorithm version. A redelivered or retried
    ``document.segment`` job recomputes the same uid, finds the row, and
    updates it in place instead of inserting a duplicate (backed by the unique
    ``(document_id, segment_uid)`` constraint). Excluding text and version
    means an unchanged structure maps to the same row across runs, while a
    reviewer-approved row is never overwritten (see ``segmentation_pipeline``).

    Geometry
    --------
    ``bbox_*`` is an **anchor box on ``start_page`` only**, computed from the
    contributing regions that live on that page. It is never a merge of boxes
    from different pages, because page-local coordinates are not comparable
    across pages. Per-region page-local geometry stays in ``OcrRegion`` and is
    linked through ``SegmentRegion``, so cross-page segments keep each region's
    true page and box.

    Text
    ----
    ``text`` is a derived copy of OCR output (assembled across contributing
    regions) so a segment is stably readable and reviewable without re-joining
    every region on each request. The source of truth remains ``OcrRegion``;
    links are retained so it can always be recomputed. ``corrected_text`` holds
    a reviewer's replacement (never written by the machine).
    """

    __tablename__ = "document_segments"

    document_id: Mapped[str] = mapped_column(
        GUID,
        ForeignKey("source_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Deterministic identity within the document (see class doc).
    segment_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    # Hierarchy: subparts/answers nest under a question (or parent subpart).
    parent_id: Mapped[Optional[str]] = mapped_column(
        GUID,
        ForeignKey("document_segments.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    segment_type: Mapped[str] = mapped_column(
        String(40), default=SegmentType.unclassified.value, nullable=False, index=True
    )
    # Optional short label as observed (e.g. "1", "a", "(ii)"), display-only.
    label: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    # Page span. Multi-page segments use start_page < end_page; boxes are
    # still page-local to start_page (never merged across pages).
    start_page: Mapped[int] = mapped_column(Integer, nullable=False)
    end_page: Mapped[int] = mapped_column(Integer, nullable=False)
    # Global reading order across the document (0-based, contiguous).
    reading_order: Mapped[int] = mapped_column(Integer, nullable=False)
    # Anchor geometry on ``start_page`` only (see class doc "Geometry").
    bbox_left: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_top: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_width: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_height: Mapped[int] = mapped_column(Integer, nullable=False)
    # Derived assembly of contributing region text (see class doc "Text").
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Reviewer replacement text; NULL until a human corrects the segment.
    corrected_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uncertainty: Mapped[str] = mapped_column(
        String(40), default=SegmentUncertainty.clear.value, nullable=False, index=True
    )
    # Segmentation algorithm + version that produced this row (provenance).
    algorithm: Mapped[str] = mapped_column(String(60), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(40), nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(30), default=ReviewStatus.pending.value, nullable=False, index=True
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # Small provenance bag (detection signals, warnings, region counts). Never
    # stores raw OCR text or storage keys.
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    __table_args__ = (
        # Segment identity within a document is unique: retries are idempotent.
        UniqueConstraint("document_id", "segment_uid", name="uq_segment_doc_uid"),
        CheckConstraint(
            "start_page >= 1 AND end_page >= start_page",
            name="ck_segment_page_span",
        ),
        CheckConstraint(
            "bbox_width >= 0 AND bbox_height >= 0", name="ck_segment_bbox_nonneg"
        ),
        # Fast document + reading-order / page retrieval for the API.
        Index("ix_segment_doc_order", "document_id", "reading_order"),
        Index("ix_segment_doc_page", "document_id", "start_page"),
    )


class SegmentRegion(IdMixin, Base):
    """Link between a segment and the OCR regions that constitute it.

    This is what keeps reconstructed text tied to its evidence: every segment
    lists the contributing ``OcrRegion`` ids in ``position`` order, across
    pages, so a reviewer can always trace assembled text back to the exact
    line observations and their page-local geometry.

    ``UNIQUE(region_id)`` is the enforced non-contradiction rule: **one OCR
    region belongs to at most one segment**, so two structures can never claim
    the same line. The segmenter assigns each input region exactly once (an
    ``unclassified`` segment catches anything no rule places), so no region is
    silently dropped either.
    """

    __tablename__ = "segment_regions"

    segment_id: Mapped[str] = mapped_column(
        GUID,
        ForeignKey("document_segments.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    region_id: Mapped[str] = mapped_column(
        GUID,
        ForeignKey("ocr_regions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Order of this region within its segment (reading order).
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("region_id", name="uq_segment_region_single_owner"),
        UniqueConstraint(
            "segment_id", "position", name="uq_segment_region_position"
        ),
        CheckConstraint("position >= 0", name="ck_segment_region_position_nonneg"),
    )


class SegmentReview(IdMixin, Base):
    """Append-only human confirmation or correction of a segment.

    Mirrors ``OcrReview``: corrections never destroy history. ``previous_text``
    keeps the effective value live before this revision, ``corrected_text`` is
    the new value (NULL for a confirmation). Reprocessing never overwrites a
    segment whose ``review_status`` has moved off ``pending``.
    """

    __tablename__ = "segment_reviews"

    segment_id: Mapped[str] = mapped_column(
        GUID,
        ForeignKey("document_segments.id", ondelete="CASCADE"),
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
    reviewer_id: Mapped[Optional[str]] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("segment_id", "revision", name="uq_segment_revision"),
        CheckConstraint("revision >= 1", name="ck_segment_review_revision_positive"),
    )
