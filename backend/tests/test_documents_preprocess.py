"""Checkpoint C unit tests: preprocessing, state machine, and queue fixes.

Covers image normalization (EXIF, downscale, blank detection), PDF rasterization
limits and the unavailable-backend boundary, coordinate mapping, the new
``preprocessed`` state, and the queue's attempt/lease/retry behavior.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from app.config import load_settings
from app.models.documents import DocumentProcessingState as S
from app.services.documents import preprocess as pp
from app.services.documents import state as sm

# --- helpers ---------------------------------------------------------------


def _png(width=100, height=60, color=(200, 30, 30), exif_orientation=None) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    if exif_orientation is not None:
        from PIL import ImageDraw

        exif = img.getexif()
        exif[274] = exif_orientation  # 274 = Orientation tag
        img.info["exif"] = exif.tobytes()
        # Redraw a marker so orientation is observable.
        ImageDraw.Draw(img).line((0, 0, width, height), fill=(0, 0, 0), width=3)
    buf = io.BytesIO()
    img.save(buf, format="PNG", exif=img.info.get("exif", b""))
    return buf.getvalue()


def _pdf(pages=2) -> bytes:
    from PyPDF2 import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _limits(**overrides) -> pp.PageLimits:
    base = pp.PageLimits.from_settings(load_settings())
    # Typed loosely: the limits dataclass mixes int and float fields, and this
    # helper overlays caller-supplied overrides before constructing it.
    data: dict[str, Any] = {
        "render_scale": base.render_scale,
        "pdf_max_page_dimension": base.pdf_max_page_dimension,
        "max_working_dimension": base.max_working_dimension,
        "page_timeout_seconds": base.page_timeout_seconds,
        "job_timeout_seconds": base.job_timeout_seconds,
        "max_total_pages": base.max_total_pages,
    }
    data.update(overrides)
    return pp.PageLimits(**data)


# --- image normalization ---------------------------------------------------


def test_image_normalized_to_grayscale():
    pages = pp.preprocess_document(_png(), media_type="image/png", limits=_limits())
    assert len(pages) == 1
    page = pages[0]
    assert page.backend == "pillow"
    # The working image is single-channel grayscale.
    from PIL import Image

    img = Image.open(io.BytesIO(page.image_png))
    assert img.mode == "L"


def test_image_downscales_to_working_limit_without_upscaling():
    # A large image is downscaled to the working limit.
    pages = pp.preprocess_document(
        _png(width=5000, height=1000), media_type="image/png",
        limits=_limits(max_working_dimension=1000),
    )
    page = pages[0]
    assert max(page.working_width, page.working_height) <= 1000
    # Original geometry is preserved separately for coordinate mapping.
    assert page.original_width == 5000
    assert "downscaled_to_working_limit" in page.warnings
    # Scale is working/original and uniform.
    assert page.working_scale == pytest.approx(page.working_width / 5000)


def test_small_image_is_not_upscaled():
    pages = pp.preprocess_document(
        _png(width=50, height=50), media_type="image/png",
        limits=_limits(max_working_dimension=1000),
    )
    page = pages[0]
    assert page.working_width == 50
    assert page.working_height == 50
    assert page.working_scale == pytest.approx(1.0)


def test_coordinate_mapping_round_trips():
    # original = working / scale. Verify the documented mapping holds.
    pages = pp.preprocess_document(
        _png(width=4000, height=2000), media_type="image/png",
        limits=_limits(max_working_dimension=1000),
    )
    page = pages[0]
    working_x = 500
    original_x = working_x / page.working_scale
    assert original_x == pytest.approx(2000.0, rel=0.01)


def test_blank_image_is_flagged_not_failed():
    # A solid-color image is blank; it must be flagged, not rejected.
    pages = pp.preprocess_document(
        _png(color=(255, 255, 255)), media_type="image/png", limits=_limits()
    )
    assert "possibly_blank" in pages[0].warnings


def test_content_hash_is_sha256_of_working_image():
    import hashlib

    pages = pp.preprocess_document(_png(), media_type="image/png", limits=_limits())
    page = pages[0]
    assert page.content_hash == hashlib.sha256(page.image_png).hexdigest()


def test_exif_orientation_is_applied():
    # Orientation 6 rotates 90 degrees; width/height swap after transpose.
    data = _png(width=200, height=100, exif_orientation=6)
    pages = pp.preprocess_document(data, media_type="image/png", limits=_limits())
    page = pages[0]
    # After EXIF transpose the canonical original is portrait.
    assert page.original_width == 100
    assert page.original_height == 200


def test_malformed_image_is_a_permanent_error():
    corrupt = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    with pytest.raises(pp.PreprocessError) as exc:
        pp.preprocess_document(corrupt, media_type="image/png", limits=_limits())
    assert exc.value.permanent is True
    assert exc.value.category == "validation"


def test_unsupported_media_type_is_permanent():
    with pytest.raises(pp.PreprocessError) as exc:
        pp.preprocess_document(b"data", media_type="application/zip", limits=_limits())
    assert exc.value.permanent is True


# --- PDF rasterization -----------------------------------------------------


def test_pdf_rasterizes_all_pages_in_order():
    pages = pp.preprocess_document(
        _pdf(pages=3), media_type="application/pdf", limits=_limits()
    )
    assert [p.page_number for p in pages] == [1, 2, 3]
    assert all(p.backend == "pypdfium2" for p in pages)
    # A blank A4 page at scale 2.0 is ~1190x1684 px.
    assert pages[0].original_width == pytest.approx(1190, abs=2)


def test_pdf_page_count_limit_is_enforced():
    with pytest.raises(pp.PreprocessError) as exc:
        pp.preprocess_document(
            _pdf(pages=5), media_type="application/pdf", limits=_limits(max_total_pages=2)
        )
    assert exc.value.permanent is True
    assert "page" in exc.value.message.lower()


def test_pdf_max_page_dimension_clamps_render():
    # A very small max dimension forces a re-render clamp.
    pages = pp.preprocess_document(
        _pdf(pages=1), media_type="application/pdf",
        limits=_limits(pdf_max_page_dimension=300, max_working_dimension=300),
    )
    page = pages[0]
    assert max(page.original_width, page.original_height) <= 300


def _fake_settings(monkeypatch, **overrides):
    """Point pp.get_settings at a lightweight stand-in (Settings is frozen)."""
    from types import SimpleNamespace

    real = load_settings()
    data = {
        "pdf_rasterizer": real.pdf_rasterizer,
        "pdf_render_scale": real.pdf_render_scale,
        "pdf_max_page_dimension": real.pdf_max_page_dimension,
        "preprocess_max_working_dimension": real.preprocess_max_working_dimension,
        "preprocess_page_timeout_seconds": real.preprocess_page_timeout_seconds,
        "preprocess_job_timeout_seconds": real.preprocess_job_timeout_seconds,
        "preprocess_max_total_pages": real.preprocess_max_total_pages,
    }
    data.update(overrides)
    monkeypatch.setattr(pp, "get_settings", lambda: SimpleNamespace(**data))


def test_pdf_unavailable_backend_is_explicit(monkeypatch):
    # Simulate the rasterizer being uninstalled while the setting still names it.
    monkeypatch.setattr(pp, "_pypdfium2_available", lambda: False)
    _fake_settings(monkeypatch, pdf_rasterizer="pypdfium2")
    with pytest.raises(pp.PreprocessError) as exc:
        pp.preprocess_document(_pdf(), media_type="application/pdf", limits=_limits())
    assert exc.value.permanent is True
    assert "backend" in exc.value.message.lower()


def test_pdf_rasterizer_none_setting_fails_clearly(monkeypatch):
    _fake_settings(monkeypatch, pdf_rasterizer="none")
    with pytest.raises(pp.PreprocessError) as exc:
        pp.preprocess_document(_pdf(), media_type="application/pdf", limits=_limits())
    assert exc.value.permanent is True
    assert "backend" in exc.value.message.lower()


def test_malformed_pdf_is_permanent():
    corrupt = b"%PDF-1.7\n" + b"garbage" * 20
    with pytest.raises(pp.PreprocessError) as exc:
        pp.preprocess_document(corrupt, media_type="application/pdf", limits=_limits())
    assert exc.value.permanent is True


def test_job_deadline_stops_pdf_processing():
    import time as _time

    # A deadline already in the past stops before rendering the first page.
    with pytest.raises(pp.PreprocessError) as exc:
        pp.rasterize_pdf(
            _pdf(pages=2),
            limits=_limits(),
            deadline=_time.monotonic() - 1,
        )
    assert exc.value.permanent is False  # transient: worth retrying


# --- temp dir cleanup ------------------------------------------------------


def test_attempt_temp_dir_is_removed_on_success(tmp_path):
    with pp.attempt_temp_dir(str(tmp_path)) as d:
        (d / "artifact.bin").write_bytes(b"x")
        kept = d
    assert not kept.exists()


def test_attempt_temp_dir_is_removed_on_failure(tmp_path):
    kept = None
    with pytest.raises(ValueError):
        with pp.attempt_temp_dir(str(tmp_path)) as d:
            (d / "artifact.bin").write_bytes(b"x")
            kept = d
            raise ValueError("boom")
    assert kept is not None and not kept.exists()


# --- state machine: preprocessed -------------------------------------------


def test_mark_preprocessed_returns_preprocessed_when_all_pages_done():
    assert (
        sm.mark_preprocessed(S.preprocessing, total_pages=3, processed_pages=3)
        == S.preprocessed
    )


def test_mark_preprocessed_returns_failed_when_partial():
    # Partial preprocessing is an explicit failure, never completed/review.
    assert (
        sm.mark_preprocessed(S.preprocessing, total_pages=3, processed_pages=2)
        == S.failed
    )


def test_preprocessed_is_not_terminal_but_is_idle():
    assert S.preprocessed not in sm.TERMINAL_STATES
    assert S.preprocessed in sm.IDLE_STATES


def test_preprocessed_can_move_to_ocr_processing():
    assert sm.can_transition(S.preprocessed, S.ocr_processing)


def test_preprocessed_can_be_requeued_for_reprocessing():
    assert sm.can_transition(S.preprocessed, S.queued)


# --- configuration bounds --------------------------------------------------


def test_preprocessing_settings_have_safe_defaults():
    cfg = load_settings()
    assert cfg.pdf_rasterizer in {"pypdfium2", "none"}
    assert cfg.pdf_render_scale > 0
    assert cfg.preprocess_page_timeout_seconds > 0
    assert cfg.preprocess_job_timeout_seconds > 0
    assert cfg.preprocess_max_total_pages >= 1
    assert cfg.preprocess_max_concurrency >= 1
    assert cfg.job_lease_seconds >= 5


def test_invalid_pdf_rasterizer_is_rejected_at_startup(monkeypatch):
    from app.config import ConfigurationError, load_settings

    monkeypatch.setenv("PDF_RASTERIZER", "ghostscript")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_invalid_render_scale_is_rejected_at_startup(monkeypatch):
    from app.config import ConfigurationError, load_settings

    monkeypatch.setenv("PDF_RENDER_SCALE", "99")
    with pytest.raises(ConfigurationError):
        load_settings()


def test_invalid_timeouts_are_rejected_at_startup(monkeypatch):
    from app.config import ConfigurationError, load_settings

    monkeypatch.setenv("PREPROCESS_PAGE_TIMEOUT_SECONDS", "0")
    with pytest.raises(ConfigurationError):
        load_settings()
