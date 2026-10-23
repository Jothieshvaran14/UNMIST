"""Provider-neutral handwriting OCR (Checkpoint D).

Layout
------
``base``          request/result/error contract + provider interface
``none``          explicit "OCR not configured" provider (fails loudly)
``fake``          deterministic test double, refused in production
``trocr``         real local TrCPU/TrOCR inference, lazily loaded
``lines``         text-line extraction (the boundary before layout detection)
``normalization`` explicit, versioned text normalization
``registry``      provider selection and per-process lifecycle

Design rules
------------
* Importing this package must stay cheap: no torch/transformers import at
  module scope, so a base install and ordinary CI never pay for the ML stack.
* Providers receive decoded pixels and geometry — never storage keys, paths, or
  tenant identifiers — and never make authorization decisions.
* TrOCR is a *line-level* recognizer. Nothing here may report "the page was
  read" when only lines were recognized; see :mod:`lines` and
  ``OcrPageResult.scope``.
"""

from __future__ import annotations

from .base import (
    OCR_PROCESSING_VERSION,
    SCOPE_TEXT_LINES,
    OcrError,
    OcrLimits,
    OcrPageInput,
    OcrPageResult,
    OcrProvider,
    OcrRegionOutput,
)
from .registry import (
    available_providers,
    get_ocr_provider,
    register_provider,
    reset_ocr_providers_for_tests,
)

__all__ = [
    "OCR_PROCESSING_VERSION",
    "SCOPE_TEXT_LINES",
    "OcrError",
    "OcrLimits",
    "OcrPageInput",
    "OcrPageResult",
    "OcrProvider",
    "OcrRegionOutput",
    "available_providers",
    "get_ocr_provider",
    "register_provider",
    "reset_ocr_providers_for_tests",
]
