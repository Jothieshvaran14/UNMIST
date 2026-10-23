"""Provider-neutral OCR contract: request, result, errors, provider interface.

This module defines the *shape* of an OCR request and an OCR result plus the
abstract provider. It deliberately knows nothing about authorization, tenancy,
storage paths, or any particular model:

  * The application layer decides **whose** page may be read. A provider is
    handed decoded pixels and geometry only — never a filesystem path, never a
    storage key, never a tenant identifier it could act on.
  * A provider never decides authorization. It cannot fetch, publish, or
    reject anything on its own; it only maps the image it was given to text.

Recognition scope (stated once, enforced by the result type)
------------------------------------------------------------
TrOCR is a **text-line recognizer**, not an examination-document understanding
system. It reads individual lines of handwriting. It does *not* detect question
numbers, associate text with questions, read tables or diagrams, reconstruct
multi-column layout, understand mathematical notation reliably, or grade
anything. ``OcrPageResult.scope`` therefore records what actually happened
(``text_lines``) alongside any warnings, so a caller can never mistake line
recognition for page understanding, and a future layout-aware provider can
report a richer scope without changing this contract.

Coordinate contract
-------------------
Providers receive the *working* page image (the persisted artifact) and return
bounding boxes in **original-image pixel space** (origin top-left, x right, y
down) — the space recorded by ``DocumentPage.width_px``/``height_px``. The
provider owns that mapping because only it knows which pixels it segmented;
``working_scale`` (working/original) is supplied so the mapping is a single,
auditable division. Boxes are validated before persistence.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ....models.documents import DocumentErrorCategory, RegionType

# Bumped whenever OCR behavior/provenance semantics change. Persisted with every
# region so results remain attributable after the pipeline evolves.
OCR_PROCESSING_VERSION = "ocr-v1"

# What today's providers can actually claim to have recognized. Kept explicit
# so "we ran OCR on lines" is never reported as "we understood this page".
SCOPE_TEXT_LINES = "text_lines"


class OcrError(Exception):
    """An OCR failure with a bounded, non-sensitive category.

    ``category`` is one of :class:`DocumentErrorCategory` (persisted verbatim);
    ``permanent`` distinguishes input/capability errors that can never succeed
    on retry (no model, unsupported image) from transient ones (timeout, I/O)
    that are worth retrying under the queue's bounded backoff.

    The message is a fixed, safe string — never an exception repr, path, or
    model output — so it can be shown to a client without leaking internals.
    """

    def __init__(
        self,
        message: str,
        *,
        category: str = DocumentErrorCategory.ocr_failed.value,
        permanent: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.category = category
        self.permanent = permanent


@dataclass(frozen=True)
class OcrLimits:
    """Resolved, validated resource limits for one OCR pass."""

    max_regions_per_page: int
    max_crop_dimension: int
    page_timeout_seconds: float
    job_timeout_seconds: float
    min_line_ink_ratio: float

    @classmethod
    def from_settings(cls, settings=None) -> "OcrLimits":
        from ....config import get_settings

        cfg = settings or get_settings()
        return cls(
            max_regions_per_page=cfg.ocr_max_regions_per_page,
            max_crop_dimension=cfg.ocr_max_crop_dimension,
            page_timeout_seconds=cfg.ocr_page_timeout_seconds,
            job_timeout_seconds=cfg.ocr_job_timeout_seconds,
            min_line_ink_ratio=cfg.ocr_min_line_ink_ratio,
        )


@dataclass(frozen=True)
class OcrPageInput:
    """Everything a provider needs to recognize one page — and nothing more.

    No storage key and no filesystem path appears here by design: the caller
    reads the private artifact itself and passes bytes, so a provider (or a bug
    inside one) cannot reach unrelated objects. Coordinates are supplied so a
    provider can map its boxes into original space without guessing.
    """

    document_id: str
    page_id: str
    page_number: int
    # The persisted *working* page image (PNG bytes) to recognize.
    image_png: bytes
    # Original-image dimensions: the coordinate space of returned boxes.
    width_px: int
    height_px: int
    # working/original scale the provider divides by to map back to original.
    working_scale: float
    # SHA-256 of the artifact, letting the provider verify what it was handed.
    content_hash: str


@dataclass(frozen=True)
class OcrRegionOutput:
    """One recognized text region, in **original-image** pixel coordinates."""

    bbox_left: int
    bbox_top: int
    bbox_width: int
    bbox_height: int
    raw_text: Optional[str]
    text: Optional[str]
    region_type: str = RegionType.text_line.value
    reading_order: Optional[int] = None
    # Provider-reported score in [0,1], or None when the provider reports none.
    # Never invented: TrOCR exposes no calibrated confidence, so it returns None
    # rather than a token probability dressed up as correctness.
    confidence: Optional[float] = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class OcrPageResult:
    """Outcome of recognizing one page.

    ``scope`` plus ``warnings`` state honestly what was (and was not)
    accomplished. ``empty_reason`` distinguishes "the page really had no
    text lines" from a failure, so an empty prediction is never silently
    promoted into a transcript.
    """

    provider: str
    model_id: str
    processing_version: str = OCR_PROCESSING_VERSION
    model_version: Optional[str] = None
    regions: tuple[OcrRegionOutput, ...] = ()
    scope: str = SCOPE_TEXT_LINES
    duration_ms: int = 0
    warnings: tuple[str, ...] = ()
    # Why there are no regions, when there are none (e.g. "blank_page",
    # "no_text_line_detected"). None when regions were produced.
    empty_reason: Optional[str] = None
    # Set when processing partially completed; the pipeline must not report
    # this page as fully recognized.
    partial: bool = False


class OcrProvider(ABC):
    """Minimal provider interface. Implementations must be side-effect free
    with respect to application state: they read pixels and return text.

    Providers are *not* thread-safe per instance unless documented otherwise;
    the registry hands out a single shared instance and the caller bounds
    concurrency (see ``ocr_max_concurrency``).
    """

    #: Stable identifier persisted on every region (e.g. ``"trocr"``).
    name: str = "base"

    @abstractmethod
    def recognize(self, page: OcrPageInput, *, limits: OcrLimits) -> OcrPageResult:
        """Recognize text regions on one page.

        Raises :class:`OcrError` for expected failures (missing model,
        unavailable dependency, timeout, unusable image). Unexpected exceptions
        are allowed to propagate so the queue records them as retryable.
        """

    def describe(self) -> dict:
        """Provenance for audit/evaluation. Must not include paths or secrets."""
        return {"provider": self.name}

    def close(self) -> None:
        """Release heavy resources (models). Default is a no-op."""


def validate_region(region: OcrRegionOutput, *, page_width: int, page_height: int) -> bool:
    """Return True if a region's box is usable in original-image space.

    Rejects zero/negative area, boxes outside the page, and boxes whose
    coordinates cannot be represented. This runs *before* persistence so
    malformed geometry (from a bad scale factor or an over-large crop) is
    dropped with a warning instead of stored as an impossible box.
    """
    if region.bbox_width <= 0 or region.bbox_height <= 0:
        return False
    if region.bbox_left < 0 or region.bbox_top < 0:
        return False
    if region.bbox_left + region.bbox_width > page_width:
        return False
    if region.bbox_top + region.bbox_height > page_height:
        return False
    return True


class Stopwatch:
    """Monotonic elapsed-time helper for reporting real durations."""

    __slots__ = ("_start",)

    def __init__(self) -> None:
        self._start = time.monotonic()

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)
