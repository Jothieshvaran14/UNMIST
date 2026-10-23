"""Deterministic test-double provider. Never selectable in production.

Purpose: exercise the *pipeline* — line extraction, coordinate mapping, region
persistence, idempotency, retry and cancellation — without a model, a download,
or a GPU. Its text is fixture-derived, **not** read from the image, so it can
never be mistaken for recognition quality.

Guard rails that make confusion impossible:

  * ``OCR_PROVIDER=fake`` is rejected by ``load_settings`` whenever
    ``ENVIRONMENT=production``; the app refuses to start rather than silently
    fabricating recognition results.
  * Every region carries ``provider="fake"`` and a ``model_id`` of
    ``fake://fixture``, and the result carries a ``fake_provider_output``
    warning, so fake text is always attributable after persistence.
  * It reports ``confidence=None``: a test double must not manufacture a score
    that could be misread as model confidence.
  * Importing this module has no ML dependency, so ordinary CI can use it.

The fake still runs the real :mod:`lines` extractor, because the geometry path
(line boxes -> original coordinates -> bounded, ordered regions) is exactly what
the pipeline tests need to validate. Only the *text* is synthetic.
"""

from __future__ import annotations

import io

from PIL import Image

from ....models.documents import DocumentErrorCategory
from .base import (
    OCR_PROCESSING_VERSION,
    OcrError,
    OcrLimits,
    OcrPageInput,
    OcrPageResult,
    OcrProvider,
    OcrRegionOutput,
    Stopwatch,
    validate_region,
)
from .lines import extract_text_lines, map_box_to_original
from .normalization import normalize_ocr_text

# Distinguishable in logs, audits, and any UI that shows provenance.
FAKE_PROVIDER_NAME = "fake"
FAKE_MODEL_ID = "fake://fixture"
FAKE_WARNING = "fake_provider_output"

# Fixture texts cycled by region index so tests can assert exact values.
_DEFAULT_TEXTS = ("alpha beta", "gamma delta", "epsilon zeta")


class FakeOcrProvider(OcrProvider):
    """Deterministic, model-free provider used only by tests.

    Parameters
    ----------
    texts:
        Cycled by region index. Defaults to a fixed tuple.
    fail_with:
        When set, every call raises this :class:`OcrError` (lets tests drive
        failure classification without monkeypatching).
    blank:
        When True, finds no lines and reports ``empty_reason="blank_page"``,
        exercising the empty-output path.
    """

    name = FAKE_PROVIDER_NAME

    def __init__(
        self,
        *,
        texts: tuple[str, ...] = _DEFAULT_TEXTS,
        fail_with: OcrError | None = None,
        blank: bool = False,
    ) -> None:
        self._texts = texts or _DEFAULT_TEXTS
        self._fail_with = fail_with
        self._blank = blank

    def recognize(self, page: OcrPageInput, *, limits: OcrLimits) -> OcrPageResult:
        if self._fail_with is not None:
            raise self._fail_with

        watch = Stopwatch()
        try:
            image = Image.open(io.BytesIO(page.image_png))
            image.load()
        except Exception as exc:  # noqa: BLE001 - classified into a safe error
            raise OcrError(
                "Page image could not be decoded for OCR",
                category=DocumentErrorCategory.validation.value,
                permanent=True,
            ) from exc

        boxes = [] if self._blank else extract_text_lines(
            image,
            min_ink_ratio=limits.min_line_ink_ratio,
            max_regions=limits.max_regions_per_page,
        )

        regions: list[OcrRegionOutput] = []
        warnings: list[str] = [FAKE_WARNING]
        dropped = 0
        for index, box in enumerate(boxes):
            mapped = map_box_to_original(
                box,
                working_scale=page.working_scale,
                original_width=page.width_px,
                original_height=page.height_px,
            )
            if mapped is None:
                dropped += 1
                continue
            left, top, width, height = mapped
            raw = self._texts[index % len(self._texts)]
            region = OcrRegionOutput(
                bbox_left=left,
                bbox_top=top,
                bbox_width=width,
                bbox_height=height,
                raw_text=raw,
                text=normalize_ocr_text(raw),
                reading_order=index,
                # A fake never reports confidence.
                confidence=None,
            )
            if not validate_region(region, page_width=page.width_px, page_height=page.height_px):
                dropped += 1
                continue
            regions.append(region)

        if dropped:
            warnings.append("regions_dropped_invalid_geometry")

        empty_reason = None
        if not regions:
            empty_reason = "blank_page" if self._blank else "no_text_line_detected"

        return OcrPageResult(
            provider=FAKE_PROVIDER_NAME,
            model_id=FAKE_MODEL_ID,
            model_version=None,
            processing_version=OCR_PROCESSING_VERSION,
            regions=tuple(regions),
            duration_ms=watch.elapsed_ms,
            warnings=tuple(warnings),
            empty_reason=empty_reason,
        )

    def describe(self) -> dict:
        return {
            "provider": FAKE_PROVIDER_NAME,
            "model_id": FAKE_MODEL_ID,
            "test_double": True,
        }


def fixture_texts() -> tuple[str, ...]:
    """Expose the default fixture texts for assertions in tests."""
    return _DEFAULT_TEXTS


__all__ = [
    "FAKE_MODEL_ID",
    "FAKE_PROVIDER_NAME",
    "FAKE_WARNING",
    "FakeOcrProvider",
    "fixture_texts",
]
