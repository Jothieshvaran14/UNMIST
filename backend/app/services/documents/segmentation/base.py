"""Segmentation domain contracts: inputs, outputs, errors, limits, identity.

Checkpoint E turns persisted OCR regions into *reviewable structure*. This
module owns the vocabulary shared by the deterministic segmenter, the durable
job pipeline, and the API — deliberately free of any persistence, FastAPI, or
ML import so the rules stay testable in isolation.

What segmentation is **not**:
  * It never mutates ``OcrRegion`` (OCR evidence is read-only here).
  * It never asserts that an answer is correct — that is assessment, out of
    scope for this milestone.
  * It never reports a calibrated probability; uncertainty is categorical
    (:class:`SegmentUncertainty`).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from ....models.documents import (
    DocumentErrorCategory,
    SegmentType,
    SegmentUncertainty,
)

# Algorithm name stamped on every segment this baseline produces.
SEGMENT_ALGORITHM = "rule_baseline"


class SegmentationError(Exception):
    """A segmentation failure with a bounded, non-sensitive category.

    ``category`` is one of :class:`DocumentErrorCategory` (persisted verbatim);
    ``permanent`` distinguishes input errors that can never succeed on retry
    (e.g. no OCR evidence, over-limit input) from transient infrastructure
    failures worth retrying.
    """

    def __init__(
        self,
        message: str,
        *,
        category: str = DocumentErrorCategory.validation.value,
        permanent: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.category = category
        self.permanent = permanent


@dataclass(frozen=True)
class SegmentLimits:
    """Resolved, validated resource limits for one segmentation run."""

    max_pages_per_job: int
    max_regions_per_document: int
    max_segments_per_document: int
    max_region_text_length: int
    job_timeout_seconds: float

    @classmethod
    def from_settings(cls, settings=None) -> "SegmentLimits":
        from ....config import get_settings

        cfg = settings or get_settings()
        return cls(
            max_pages_per_job=cfg.segment_max_pages_per_job,
            max_regions_per_document=cfg.segment_max_regions_per_document,
            max_segments_per_document=cfg.segment_max_segments_per_document,
            max_region_text_length=cfg.segment_max_region_text_length,
            job_timeout_seconds=cfg.segment_job_timeout_seconds,
        )


@dataclass(frozen=True)
class RegionInput:
    """One OCR region handed to the segmenter — geometry + text only.

    Carries no storage key or filesystem path by design: the caller reads
    region rows itself and passes only what the rules need. ``bbox_*`` is in
    original-image pixels and is **page-local** (``page_number`` says which
    page); boxes from different pages are never combined.
    """

    region_id: str
    page_number: int
    reading_order: int
    bbox_left: int
    bbox_top: int
    bbox_width: int
    bbox_height: int
    text: str
    region_type: str


@dataclass(frozen=True)
class PageInput:
    """Page geometry needed to interpret region positions (furniture detection)."""

    page_number: int
    width_px: Optional[int]
    height_px: Optional[int]


@dataclass
class SegmentCandidate:
    """One inferred structural segment, before persistence.

    ``region_ids`` are ordered by reading order and span pages for multi-page
    segments. ``parent_uid`` links subparts/answers to their question (resolved
    to a foreign key by the pipeline). ``bbox_*`` is the **anchor box on
    ``start_page`` only** — computed from regions on that page, never a merge
    across pages.
    """

    segment_type: SegmentType
    uncertainty: SegmentUncertainty
    start_page: int
    end_page: int
    reading_order: int
    bbox_left: int
    bbox_top: int
    bbox_width: int
    bbox_height: int
    text: str
    region_ids: list[str] = field(default_factory=list)
    label: Optional[str] = None
    # Deterministic uid resolved by the segmenter (see derive_segment_uid).
    segment_uid: Optional[str] = None
    parent_uid: Optional[str] = None
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SegmentationResult:
    """Everything the segmenter derived for one document."""

    segments: tuple[SegmentCandidate, ...]
    # Input regions placed into a segment — equals all inputs (no silent drop).
    covered_region_ids: tuple[str, ...]
    # Non-fatal notes (e.g. "over segment cap", "empty input"), never text.
    warnings: tuple[str, ...] = ()


def derive_segment_uid(*, first_region_id: str, segment_type: SegmentType) -> str:
    """Deterministic identity for one segment within a document.

    Derived from the **first contributing region** and the segment type — never
    from recognized text, page images, or the algorithm version:

    * Text-independent, so re-recognizing or re-assembling the same structure
      maps onto the same row and updates it instead of inserting a duplicate.
    * Version-independent, so a new algorithm version re-tags machine segments
      in place while a reviewer-approved row (different uid once its structure
      moves) is preserved rather than clobbered.
    * Anchor-region-based, so adding/removing an *earlier* segment does not
      re-key every later one (reading-order-based ids would).

    ``first_region_id`` is a globally unique region primary key, so two
    segments can never collide: each region belongs to at most one segment.
    """
    basis = f"{first_region_id}|{segment_type.value}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:64]
