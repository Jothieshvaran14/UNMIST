"""Document segmentation (Checkpoint E): OCR regions -> reviewable structure.

Layering (mirrors the OCR package):

* :mod:`~.segmentation.base` — vocabulary shared by segmenter, pipeline, and
  API (no persistence / FastAPI / ML imports).
* :mod:`~.segmentation.rules` — versioned, configurable heuristics.
* :mod:`~.segmentation.segmenter` — the deterministic baseline engine (pure).

The durable orchestration lives one level up in
:mod:`~..segmentation_pipeline`. Segmentation consumes persisted OCR regions
and never mutates them; assessment/grading stays out of scope.
"""

from __future__ import annotations

from .base import (
    SEGMENT_ALGORITHM,
    PageInput,
    RegionInput,
    SegmentationError,
    SegmentationResult,
    SegmentCandidate,
    SegmentLimits,
    derive_segment_uid,
)
from .rules import SegmentationRules
from .segmenter import BaselineSegmenter

__all__ = [
    "SEGMENT_ALGORITHM",
    "BaselineSegmenter",
    "PageInput",
    "RegionInput",
    "SegmentCandidate",
    "SegmentLimits",
    "SegmentationError",
    "SegmentationResult",
    "SegmentationRules",
    "derive_segment_uid",
]
