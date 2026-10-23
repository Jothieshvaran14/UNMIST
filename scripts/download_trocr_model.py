"""Pre-populate the local Hugging Face cache with the TrOCR model.

Explicit, one-time, and never invoked at application startup: the application
and ordinary CI never download anything. Run manually when genuine inference is
wanted:

    python scripts/download_trocr_model.py

Refuses to run when OCR_LOCAL_FILES_ONLY=1 (the "no network" setting) so the
no-download guarantee cannot be bypassed by accident. Verifies the repository
license metadata before fetching, and reports the exact artifact sizes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Disable Hugging Face's Xet transfer backend *before* importing
# huggingface_hub/transformers. The Xet CAS service was unreliable for the large
# ~1.27 GiB weights download (repeated "CAS service error ... Request failed
# after 5 retries"); the classic HTTPS range-request path resumes and completes
# reliably. This only affects this manual script, never application startup.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main() -> int:
    if os.environ.get("OCR_LOCAL_FILES_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}:
        print("OCR_LOCAL_FILES_ONLY is set; refusing to download.", file=sys.stderr)
        return 2

    from app.config import get_settings

    settings = get_settings()
    model_id = settings.ocr_model_id

    # Verify license metadata from the official repository before fetching.
    import httpx

    try:
        meta = httpx.get(
            f"https://huggingface.co/api/models/{model_id}", timeout=30.0
        )
        meta.raise_for_status()
        info = meta.json()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not verify model metadata: {exc}", file=sys.stderr)
        return 1

    license_id = (info.get("cardData") or {}).get("license") or "unknown"
    gated = info.get("gated", False)
    print(f"model     : {model_id}")
    print(f"license   : {license_id}")
    print(f"gated     : {gated}")
    if gated:
        print("Repository is gated; a Hugging Face token would be required.", file=sys.stderr)
        return 1
    if license_id != "mit":
        print(f"Unexpected license {license_id!r}; review before downloading.", file=sys.stderr)
        return 1

    print("Downloading model artifacts (this can take several minutes)...")
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    cache_dir = settings.ocr_model_cache_dir or None
    # Load the processor first: it is small and confirms the tokenizer/config
    # side is intact before the ~1.27 GiB weights download begins.
    processor = TrOCRProcessor.from_pretrained(model_id, cache_dir=cache_dir)
    print(f"Processor loaded: {type(processor).__name__}")
    model = VisionEncoderDecoderModel.from_pretrained(model_id, cache_dir=cache_dir)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Loaded: {type(model).__name__} with {n_params:,} parameters")
    print("Cache is now populated; the opt-in smoke test can run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
