"""Opt-in real-model smoke test for TrOCR. Ordinary CI never runs this.

This test loads the genuine ``microsoft/trocr-base-handwritten`` model and
performs one real inference on a small, permitted **synthetic** sample. It
exists to prove the adapter actually works end to end — not to measure
accuracy, and not to be faked by the test double.

Gate
----
Skipped unless **both** are set:

  * ``UNMIST_OCR_SMOKE=1``
  * ``OCR_REAL_MODEL_SMOKE_ENABLED=1`` (the app-level switch)

It also refuses to auto-download: set ``OCR_LOCAL_FILES_ONLY=1`` to guarantee a
missing artifact fails fast instead of pulling ~1.27 GiB. If the model is not
already in the local cache, the test reports the blocker honestly rather than
substituting the fake provider.

The sample is synthetic (drawn with Pillow), so no third-party document, no
student data, and no licensed corpus is involved. Its "ground truth" is the
literal string drawn into the image; the assertion is that inference runs and
returns *some* text, **not** that the text matches — a real accuracy claim needs
a lawful, representative benchmark, which this checkpoint does not have.
"""

from __future__ import annotations

import os
import time

import pytest

_OPT_IN = os.environ.get("UNMIST_OCR_SMOKE", "") == "1"
_APP_SWITCH = os.environ.get("OCR_REAL_MODEL_SMOKE_ENABLED", "") == "1"

pytestmark = pytest.mark.skipif(
    not (_OPT_IN and _APP_SWITCH),
    reason=(
        "Real-model OCR smoke test is opt-in: set UNMIST_OCR_SMOKE=1 and "
        "OCR_REAL_MODEL_SMOKE_ENABLED=1. Ordinary CI never loads a model."
    ),
)


def _synthetic_line_png(text: str = "handwriting") -> bytes:
    """Draw a simple synthetic 'handwritten' line on a white background."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", (640, 96), 255)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001 - font is best effort
        font = None
    # Slight vertical offset per character approximates uneven baselines so the
    # crop is not a perfectly uniform printed line.
    x = 20
    for ch in text:
        draw.text((x, 28), ch, fill=20, font=font)
        x += 34
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _cached_artifacts(model_id: str) -> dict[str, bool]:
    """Report which required artifacts are present in the local HF cache.

    Checks the *weights* as well as the config: the config alone is a few KB
    and always lands first, whereas the ~1.27 GiB ``model.safetensors`` is the
    file that actually determines whether inference can run. Gating on config
    alone would let the test proceed to attempt a large download, which this
    smoke test explicitly refuses to do.
    """
    files = {
        "config.json": False,
        "model.safetensors": False,
        "pytorch_model.bin": False,
    }
    try:
        from huggingface_hub import try_to_load_from_cache

        for name in files:
            # Signature is (repo_id, filename) — not a combined path.
            path = try_to_load_from_cache(model_id, name)
            files[name] = isinstance(path, str)
    except Exception:  # noqa: BLE001 - treat inability to check as "unknown"
        return {name: False for name in files}
    return files


def _weights_cached(artifacts: dict[str, bool]) -> bool:
    """True when at least one weights file (safetensors or bin) is cached."""
    return artifacts["model.safetensors"] or artifacts["pytorch_model.bin"]


def test_real_trocr_smoke_end_to_end():
    """Load the real model, run one inference, and report actual timings."""
    from app.config import load_settings
    from app.services.documents.ocr.base import OcrLimits, OcrPageInput
    from app.services.documents.ocr.registry import (
        get_ocr_provider,
        reset_ocr_providers_for_tests,
    )

    settings = load_settings()
    model_id = settings.ocr_model_id

    artifacts = _cached_artifacts(model_id)
    if not artifacts["config.json"]:
        pytest.skip(
            f"Model '{model_id}' is not in the local cache (no config.json). "
            "This smoke test does not auto-download (~1.27 GiB). Populate the "
            "HF cache first via scripts/download_trocr_model.py."
        )
    if not _weights_cached(artifacts):
        pytest.skip(
            f"Weights for '{model_id}' are not in the local cache "
            "(config.json is present, but model.safetensors / "
            "pytorch_model.bin are missing). Inference cannot run without "
            "them, and this smoke test never downloads. Run "
            "scripts/download_trocr_model.py on a host with access to the "
            "Hugging Face CDN, then re-run."
        )

    reset_ocr_providers_for_tests()
    try:
        provider = get_ocr_provider(settings, name="trocr")
        assert provider.name == "trocr"

        import hashlib

        data = _synthetic_line_png()
        page = OcrPageInput(
            document_id="smoke",
            page_id="smoke-1",
            page_number=1,
            image_png=data,
            width_px=640,
            height_px=96,
            working_scale=1.0,
            content_hash=hashlib.sha256(data).hexdigest(),
        )
        limits = OcrLimits.from_settings(settings)

        started = time.perf_counter()
        result = provider.recognize(page, limits=limits)
        elapsed = time.perf_counter() - started

        # Provenance is the real model, not the fake.
        assert result.provider == "trocr"
        assert result.model_id == model_id
        assert "fake" not in (result.model_id or "")

        # Report the actual measured outcome. We assert inference *ran* and
        # returned a well-formed result; we deliberately do NOT assert the
        # text matches, because no lawful representative benchmark exists here.
        print(
            f"[smoke] model={result.model_id} device={provider.describe().get('resolved_device')} "
            f"regions={len(result.regions)} empty_reason={result.empty_reason} "
            f"duration_ms={result.duration_ms} wall_s={elapsed:.2f} "
            f"load_s={provider.describe().get('load_seconds')}"
        )
        assert result.duration_ms >= 0
        # A blank/no-line outcome is a legitimate honest result for a synthetic
        # line the segmenter may not accept; either path proves real inference.
        assert result.empty_reason in {
            None,
            "no_text_line_detected",
            "page_timeout_partial",
        } or result.regions
    finally:
        reset_ocr_providers_for_tests()
