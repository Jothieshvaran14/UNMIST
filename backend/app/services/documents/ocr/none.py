"""The explicit "no OCR configured" provider.

``OCR_PROVIDER=none`` is the default. Requesting OCR with this provider is a
*clear, loud failure*, never a silent empty result: recognizing text without a
recognizer is impossible, and pretending otherwise (empty strings, fabricated
transcripts, a silent fall-through to an LLM) would corrupt everything
downstream of it.

It also serves as the honest answer to "why does this document have no text?":
the document is left in ``preprocessed`` (pages prepared, no OCR ran), which is
exactly what happened.
"""

from __future__ import annotations

from ....models.documents import DocumentErrorCategory
from .base import OcrError, OcrLimits, OcrPageInput, OcrPageResult, OcrProvider


class NoneOcrProvider(OcrProvider):
    """Fails explicitly: OCR was requested but no provider is configured."""

    name = "none"

    def recognize(self, page: OcrPageInput, *, limits: OcrLimits) -> OcrPageResult:
        # Permanent: retrying cannot conjure a recognizer.
        raise OcrError(
            "OCR is not configured; set OCR_PROVIDER to a configured provider",
            category=DocumentErrorCategory.ocr_unavailable.value,
            permanent=True,
        )

    def describe(self) -> dict:
        return {"provider": self.name, "configured": False}
