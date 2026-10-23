"""Document-ingestion request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentResponse(BaseModel):
    """A persisted uploaded document.

    Deliberately exposes no storage key and no signed URL: originals are private
    and are only ever served through an authorized API path, never a public link.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_user_id: Optional[str]
    org_id: Optional[str]
    original_filename: Optional[str]
    media_type: str
    content_hash: str
    size_bytes: int
    page_count: int
    processing_state: Optional[str]
    error_category: Optional[str]
    error_detail: Optional[str]
    last_job_id: Optional[str]
    created_at: datetime
    updated_at: datetime


class DocumentProcessingStatus(BaseModel):
    """Safe, non-sensitive processing status for an uploaded document.

    Exposes only coarse lifecycle facts needed by a client to poll progress.
    Never carries filesystem paths, private storage keys, stack traces, or raw
    exception details.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    processing_state: Optional[str]
    page_count: int
    # Pages actually persisted so far (reliable progress signal).
    pages_processed: int
    # Whether preprocessing finished and artifacts are ready for a later OCR.
    artifacts_ready: bool
    # Whether a client may explicitly request a retry (failed / retryable).
    retry_available: bool
    error_category: Optional[str]
    error_detail: Optional[str]
    last_job_id: Optional[str]


class OcrRegionResponse(BaseModel):
    """One recognized region, safe to return to an authorized owner.

    Carries provenance (provider/model/processing version), geometry in
    original-image pixels, and both the raw and normalized text so a reviewer
    can always see the model's verbatim output next to any normalization.

    ``confidence`` is the provider's own score or ``NULL`` when the provider
    reported none. It is never a correctness guarantee: TrOCR reports no
    calibrated confidence, so its regions are always ``NULL`` here. No storage
    key, filesystem path, or stack trace is ever included.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    page_id: str
    region_type: str
    reading_order: Optional[int]
    bbox_left: int
    bbox_top: int
    bbox_width: int
    bbox_height: int
    # Raw provider output, verbatim.
    raw_text: Optional[str]
    # Normalized provider output (whitespace only; see ocr.normalization).
    text: Optional[str]
    confidence: Optional[float]
    provider: str
    model_id: str
    model_version: Optional[str]
    processing_version: str
    review_status: str
    created_at: datetime
    updated_at: datetime


class SegmentRegionRef(BaseModel):
    """One OCR region contributing to a segment — evidence, not interpretation.

    Carries the region id, its **page-local** geometry, and its position within
    the segment, so assembled text can always be traced back to the exact line
    observation on the exact page. Boxes from different pages are never
    combined into one coordinate system.
    """

    model_config = ConfigDict(from_attributes=True)

    region_id: str
    page_number: int
    position: int
    bbox_left: int
    bbox_top: int
    bbox_width: int
    bbox_height: int
    # Verbatim recognized text for this line, so the reviewer sees the evidence.
    text: Optional[str]
    review_status: str


class SegmentResponse(BaseModel):
    """One question/answer candidate, in reading order.

    ``uncertainty`` is **categorical**, not a probability: see
    ``SegmentUncertainty`` for the meaning of each value. ``text`` is a derived
    assembly of contributing OCR regions; ``corrected_text`` is a reviewer's
    replacement and always wins when present. Nothing here states whether the
    answer is correct — assessment is out of scope.

    ``bbox_*`` is an anchor box on ``start_page`` only, never a cross-page merge.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    parent_id: Optional[str]
    segment_type: str
    label: Optional[str]
    start_page: int
    end_page: int
    reading_order: int
    bbox_left: int
    bbox_top: int
    bbox_width: int
    bbox_height: int
    text: Optional[str]
    # Effective text a client should render: reviewer correction if present.
    effective_text: Optional[str]
    corrected_text: Optional[str]
    uncertainty: str
    algorithm: str
    algorithm_version: str
    review_status: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    region_count: int
    # The contributing OCR regions, each with its own page-local geometry, so
    # assembled text is always traceable back to observed evidence.
    region_refs: list[SegmentRegionRef] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SegmentationStatusResponse(BaseModel):
    """Safe segmentation status for an owned document.

    Mirrors ``DocumentProcessingStatus``: only lifecycle facts and a bounded
    error category/detail. Never carries storage keys, paths, or stack traces.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    segmentation_status: str
    segmentation_version: Optional[str]
    segmentation_error_category: Optional[str]
    segmentation_error_detail: Optional[str]
    last_segment_job_id: Optional[str]
    segmentation_run: int
    # Whether a client may explicitly request segmentation now.
    segment_available: bool
    # Counts for progress/UX; empty when segmentation has not produced output.
    total_segments: int
    pending_review: int
    reviewed: int


class SegmentReviewRequest(BaseModel):
    """Reviewer confirmation or correction of one segment.

    ``action="confirm"`` records that a human verified the structure/text;
    ``action="correct"`` also requires ``corrected_text``. Both are append-only
    in ``segment_reviews`` and are never overwritten by reprocessing.
    """

    action: str  # "confirm" | "correct"
    corrected_text: Optional[str] = None
    reason: Optional[str] = None


class SegmentReviewResponse(BaseModel):
    """Acknowledgement of a recorded segment review."""

    model_config = ConfigDict(from_attributes=True)

    segment_id: str
    revision: int
    review_status: str
    effective_text: Optional[str]
    reviewed_at: datetime
