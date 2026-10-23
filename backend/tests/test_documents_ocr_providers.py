"""Checkpoint D unit tests: OCR provider contract, geometry, and metrics.

These are fast and DB-free. They cover the provider-neutral contract, the
``none``/``fake`` providers, text normalization, bounding-box validation,
coordinate mapping, line extraction, provider selection and its production
guard, and the CER/WER evaluation metrics.

No model is loaded and nothing is downloaded: the real TrOCR path is exercised
only by the opt-in smoke test (``test_documents_ocr_smoke.py``).
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from app.config import ConfigurationError, load_settings
from app.models.documents import DocumentErrorCategory
from app.services.documents.ocr import base
from app.services.documents.ocr.fake import FAKE_MODEL_ID, FAKE_WARNING, FakeOcrProvider
from app.services.documents.ocr.lines import (
    LineBox,
    extract_text_lines,
    map_box_to_original,
)
from app.services.documents.ocr.none import NoneOcrProvider
from app.services.documents.ocr.normalization import (
    is_effectively_empty,
    normalize_ocr_text,
)
from app.services.documents.ocr.registry import (
    available_providers,
    get_ocr_provider,
    reset_ocr_providers_for_tests,
)
from app.services.evaluation import (
    build_report,
    cer,
    corpus_cer,
    corpus_wer,
    edit_distance,
    evaluate_pairs,
    render_summary,
    tokenize_words,
    wer,
)

# --- helpers ---------------------------------------------------------------


def _line_page(width=900, height=700, bands=((80,), (220,), (360,))) -> bytes:
    """A synthetic page with dark text-like bands on white."""
    from PIL import Image, ImageDraw

    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    for (y,) in bands:
        x = 60
        while x < 700:
            w = 14 + (x % 5) * 6
            draw.rectangle([x, y, x + w, y + 26], fill=25)
            x += w + 10
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _blank_page(size=400) -> bytes:
    from PIL import Image

    img = Image.new("L", (size, size), 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _limits(**overrides) -> base.OcrLimits:
    base_limits = base.OcrLimits.from_settings(load_settings())
    # Typed loosely: the limits dataclass mixes int and float fields, and this
    # helper overlays caller-supplied overrides before constructing it.
    data: dict[str, Any] = {
        "max_regions_per_page": base_limits.max_regions_per_page,
        "max_crop_dimension": base_limits.max_crop_dimension,
        "page_timeout_seconds": base_limits.page_timeout_seconds,
        "job_timeout_seconds": base_limits.job_timeout_seconds,
        "min_line_ink_ratio": base_limits.min_line_ink_ratio,
    }
    data.update(overrides)
    return base.OcrLimits(**data)


def _page_input(data: bytes, *, width=900, height=700, scale=1.0) -> base.OcrPageInput:
    import hashlib

    return base.OcrPageInput(
        document_id="doc-1",
        page_id="page-1",
        page_number=1,
        image_png=data,
        width_px=width,
        height_px=height,
        working_scale=scale,
        content_hash=hashlib.sha256(data).hexdigest(),
    )


@pytest.fixture(autouse=True)
def _clean_providers():
    """Drop cached provider instances around every test in this module."""
    reset_ocr_providers_for_tests()
    yield
    reset_ocr_providers_for_tests()


# --- normalization ---------------------------------------------------------


def test_normalization_collapses_whitespace_only():
    assert normalize_ocr_text("  hello \t world \r\n") == "hello world"


def test_normalization_preserves_meaningful_characters():
    """Signs, decimals, punctuation, and units are never stripped."""
    tricky = "x = -3.5 m/s; y² ≈ 0.75 (±0.01)"
    assert normalize_ocr_text(tricky) == tricky


def test_normalization_preserves_internal_newlines():
    assert normalize_ocr_text("line one\nline two") == "line one\nline two"


def test_normalization_none_stays_none():
    assert normalize_ocr_text(None) is None


def test_effectively_empty():
    assert is_effectively_empty(None) is True
    assert is_effectively_empty("   ") is True
    assert is_effectively_empty("\n\t ") is True
    assert is_effectively_empty("a") is False


# --- bounding-box validation ----------------------------------------------


def _region(left, top, width, height):
    return base.OcrRegionOutput(
        bbox_left=left, bbox_top=top, bbox_width=width, bbox_height=height,
        raw_text="x", text="x",
    )


def test_region_validation_accepts_in_bounds():
    assert base.validate_region(_region(10, 10, 50, 20), page_width=100, page_height=100)


@pytest.mark.parametrize(
    "left,top,width,height",
    [
        (0, 0, 0, 10),       # zero width
        (0, 0, 10, 0),       # zero height
        (-5, 0, 10, 10),     # negative left
        (0, -5, 10, 10),     # negative top
        (95, 0, 10, 10),     # overflows right edge
        (0, 95, 10, 10),     # overflows bottom edge
    ],
)
def test_region_validation_rejects_malformed(left, top, width, height):
    assert not base.validate_region(
        _region(left, top, width, height), page_width=100, page_height=100
    )


# --- coordinate mapping ----------------------------------------------------


def test_map_box_identity_when_unscaled():
    box = LineBox(left=10, top=20, width=30, height=40)
    assert map_box_to_original(
        box, working_scale=1.0, original_width=1000, original_height=800
    ) == (10, 20, 30, 40)


def test_map_box_scales_back_to_original():
    """working/original = 0.5 means original coords are double the working ones."""
    box = LineBox(left=100, top=50, width=200, height=20)
    mapped = map_box_to_original(
        box, working_scale=0.5, original_width=1000, original_height=800
    )
    assert mapped == (200, 100, 400, 40)


def test_map_box_never_exceeds_page_bounds():
    """A box at the working edge maps inside the original page, never past it."""
    box = LineBox(left=99, top=99, width=1, height=1)
    left, top, width, height = map_box_to_original(
        box, working_scale=0.1, original_width=100, original_height=100
    )
    assert left + width <= 100
    assert top + height <= 100


def test_map_box_rejects_degenerate_scale_and_page():
    box = LineBox(left=0, top=0, width=10, height=10)
    assert (
        map_box_to_original(
            box, working_scale=0.0, original_width=100, original_height=100
        )
        is None
    )
    assert (
        map_box_to_original(
            box, working_scale=-1.0, original_width=100, original_height=100
        )
        is None
    )
    assert (
        map_box_to_original(
            box, working_scale=1.0, original_width=0, original_height=100
        )
        is None
    )


# --- line extraction -------------------------------------------------------


def test_line_extraction_finds_bands():
    from PIL import Image

    img = Image.open(io.BytesIO(_line_page())).convert("L")
    boxes = extract_text_lines(img, min_ink_ratio=0.008, max_regions=64)
    assert len(boxes) == 3
    # Ordered top-to-bottom (reading order for single-column text).
    assert [b.top for b in boxes] == sorted(b.top for b in boxes)


def test_line_extraction_blank_page_returns_no_boxes():
    from PIL import Image

    img = Image.open(io.BytesIO(_blank_page())).convert("L")
    assert extract_text_lines(img, min_ink_ratio=0.008, max_regions=64) == []


def test_line_extraction_respects_max_regions():
    from PIL import Image

    img = Image.open(io.BytesIO(_line_page())).convert("L")
    assert len(extract_text_lines(img, min_ink_ratio=0.008, max_regions=2)) == 2


def test_line_extraction_rejects_zero_max_regions():
    from PIL import Image

    img = Image.open(io.BytesIO(_line_page())).convert("L")
    assert extract_text_lines(img, min_ink_ratio=0.008, max_regions=0) == []


# --- none provider ---------------------------------------------------------


def test_none_provider_fails_explicitly():
    provider = NoneOcrProvider()
    with pytest.raises(base.OcrError) as excinfo:
        provider.recognize(_page_input(_line_page()), limits=_limits())
    assert excinfo.value.category == DocumentErrorCategory.ocr_unavailable.value
    assert excinfo.value.permanent is True


def test_none_provider_describes_not_configured():
    assert NoneOcrProvider().describe() == {"provider": "none", "configured": False}


# --- fake provider ---------------------------------------------------------


def test_fake_provider_returns_regions_with_provenance():
    provider = FakeOcrProvider()
    result = provider.recognize(_page_input(_line_page()), limits=_limits())
    assert result.provider == "fake"
    assert result.model_id == FAKE_MODEL_ID
    assert FAKE_WARNING in result.warnings
    assert len(result.regions) == 3
    # Boxes are in original space and inside the page.
    for region in result.regions:
        assert base.validate_region(region, page_width=900, page_height=700)
    # A fake must never manufacture confidence.
    assert all(r.confidence is None for r in result.regions)


def test_fake_provider_text_is_fixture_deterministic():
    provider = FakeOcrProvider(texts=("alpha beta", "gamma delta"))
    result = provider.recognize(_page_input(_line_page()), limits=_limits())
    assert [r.text for r in result.regions] == ["alpha beta", "gamma delta", "alpha beta"]


def test_fake_provider_blank_page_reports_empty_reason():
    provider = FakeOcrProvider(blank=True)
    result = provider.recognize(_page_input(_blank_page()), limits=_limits())
    assert result.regions == ()
    assert result.empty_reason == "blank_page"


def test_fake_provider_can_be_configured_to_fail():
    err = base.OcrError(
        "simulated", category=DocumentErrorCategory.ocr_timeout.value, permanent=False
    )
    provider = FakeOcrProvider(fail_with=err)
    with pytest.raises(base.OcrError) as excinfo:
        provider.recognize(_page_input(_line_page()), limits=_limits())
    assert excinfo.value.category == DocumentErrorCategory.ocr_timeout.value


def test_fake_provider_rejects_undecodable_image():
    provider = FakeOcrProvider()
    with pytest.raises(base.OcrError) as excinfo:
        provider.recognize(_page_input(b"not an image"), limits=_limits())
    assert excinfo.value.category == DocumentErrorCategory.validation.value
    assert excinfo.value.permanent is True


# --- provider selection ----------------------------------------------------


def test_default_provider_is_none():
    assert load_settings().ocr_provider == "none"
    assert get_ocr_provider().name == "none"


def test_available_providers_are_registered():
    names = available_providers()
    assert {"none", "fake", "trocr"} <= set(names)


def test_unknown_provider_is_permanent_unavailable():
    with pytest.raises(base.OcrError) as excinfo:
        get_ocr_provider(name="does-not-exist")
    assert excinfo.value.permanent is True
    assert excinfo.value.category == DocumentErrorCategory.ocr_unavailable.value


def test_fake_provider_refused_in_production():
    """The test double must never be selectable in a production configuration."""
    from dataclasses import replace

    from app.config import load_settings as _load

    production = replace(_load(), environment="production")
    with pytest.raises(base.OcrError) as excinfo:
        get_ocr_provider(production, name="fake")
    assert excinfo.value.permanent is True


def test_fake_provider_rejected_at_config_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "a-real-production-secret-key-that-is-long")
    monkeypatch.setenv("OCR_PROVIDER", "fake")
    from app.config import reset_settings_for_tests

    reset_settings_for_tests()
    with pytest.raises(ConfigurationError):
        load_settings()
    reset_settings_for_tests()
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("OCR_PROVIDER", raising=False)


def test_invalid_ocr_device_rejected(monkeypatch):
    monkeypatch.setenv("OCR_DEVICE", "tpu")
    from app.config import reset_settings_for_tests

    reset_settings_for_tests()
    with pytest.raises(ConfigurationError):
        load_settings()
    reset_settings_for_tests()
    monkeypatch.delenv("OCR_DEVICE", raising=False)


# --- evaluation metrics ----------------------------------------------------


def test_edit_distance_known_values():
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "") == 0
    assert edit_distance("", "abc") == 3
    assert edit_distance("abc", "") == 3
    assert edit_distance("same", "same") == 0


def test_cer_values_and_empty_policy():
    assert cer("hello", "hello") == 0.0
    assert cer("hello", "hellp") == pytest.approx(0.2)
    assert cer("", "") == 0.0
    assert cer("", "abc") == 1.0
    # None is treated as empty rather than raising.
    assert cer(None, None) == 0.0


def test_cer_can_exceed_one_for_long_hypothesis():
    """Over-generation is penalised, not clipped."""
    assert cer("a", "aaaa") == pytest.approx(3.0)


def test_corpus_cer_is_micro_averaged():
    # Distances 0 and 1, reference lengths 5 and 3 -> 1/8, not mean(0, 1/3).
    assert corpus_cer([("hello", "hello"), ("abc", "abd")]) == pytest.approx(0.125)


def test_wer_values_and_tokenization():
    assert wer("a b c", "a b c") == 0.0
    assert wer("a b c", "a x c") == pytest.approx(1 / 3)
    # Losing a word boundary counts as a word error.
    assert wer("a b", "ab") == pytest.approx(0.5)
    assert wer("", "") == 0.0
    assert wer("", "a b") == 1.0
    assert tokenize_words("  one   two \n three ") == ["one", "two", "three"]
    assert tokenize_words(None) == []


def test_corpus_wer_is_micro_averaged():
    assert corpus_wer([("a b c", "a b c"), ("x y", "x z")]) == pytest.approx(0.2)


def test_report_marks_fake_provider_as_not_real():
    metrics = evaluate_pairs([("hello", "helo")])
    report = build_report(metrics=metrics, provider="fake", model_id=FAKE_MODEL_ID)
    assert report["is_real_model"] is False
    assert "NOT model accuracy" in render_summary(report)


def test_report_marks_real_provider_as_real():
    metrics = evaluate_pairs([("hello", "helo")])
    report = build_report(
        metrics=metrics, provider="trocr", model_id="microsoft/trocr-base-handwritten"
    )
    assert report["is_real_model"] is True
    assert "NOT model accuracy" not in render_summary(report)


def test_evaluate_pairs_detail_is_auditable():
    metrics = evaluate_pairs([("hello", "helo")])
    assert metrics["samples"] == 1
    assert metrics["detail"][0]["char_edit_distance"] == 1
    assert metrics["empty_references"] == 0
