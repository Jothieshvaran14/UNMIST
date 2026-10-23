"""Real local TrOCR handwriting recognition (lazy, CPU-first).

Model: ``microsoft/trocr-base-handwritten`` (default), verified against the
official Hugging Face model card and repository metadata:

  * License: MIT (``license: mit`` in the model card YAML and repository API).
  * Architecture: ``VisionEncoderDecoderModel`` (BEiT image encoder + RoBERTa
    text decoder); ~333M parameters, ``model.safetensors`` = 1,333,384,464
    bytes (~1.27 GiB).
  * Intended use, per the model card: OCR on **single text-line images**. It is
    not a page-layout engine, which is why :mod:`lines` feeds it line crops.
  * Training data: the IAM handwriting database. Its terms were **not**
    re-verified here (the IAM host was unreachable during this checkpoint), so
    downstream licensing review must confirm training-data terms before any
    commercial deployment decision. The *model weights* being MIT does not by
    itself settle training-data rights.

Runtime properties

  * **Lazy.** ``torch``/``transformers`` are imported and the model is loaded
    on the first ``recognize`` call, never at import time and never at app
    startup, so a base install and ordinary CI never pay for the ML stack.
  * **Load once.** A process-wide lock makes concurrent first use load the model
    exactly once; the loaded model is reused across pages and requests.
  * **CPU-first.** ``OCR_DEVICE=auto`` resolves to CUDA only when CUDA is
    actually available; otherwise CPU. Explicit ``cpu``/``cuda`` are honoured,
    and an unavailable explicit CUDA request is a clear permanent error rather
    than a silent downgrade.
  * **No download at startup.** ``OCR_LOCAL_FILES_ONLY=true`` makes a missing
    artifact a fast, explicit capability error; otherwise the first inference
    may populate the HF cache, and only then.
  * **No invented confidence.** TrOCR emits no calibrated confidence, so every
    region reports ``confidence=None`` rather than a token score relabelled as
    correctness.

Timeout honesty: a single native ``model.generate`` call cannot be reliably
interrupted by a Python timer. The per-page and per-job budgets are therefore
enforced *between* crops; one pathological crop can overrun its budget, and the
queue's lease + ``reclaim_expired`` provide the outer bound.
"""

from __future__ import annotations

import io
import threading
import time
from typing import Any, Optional

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
from .normalization import is_effectively_empty, normalize_ocr_text

DEFAULT_MODEL_ID = "microsoft/trocr-base-handwritten"
PROVIDER_NAME = "trocr"

# TrOCR's processor resizes crops internally; this bounds what we hand it so a
# degenerate box cannot allocate an enormous tensor.
_PROCESSOR_MAX_SIDE = 4096


def _import_ml() -> tuple[Any, Any, Any]:
    """Import torch/transformers lazily, classifying any failure.

    Kept as a function (not module scope) so importing this module never pulls
    in the ML stack.
    """
    try:
        import torch
    except Exception as exc:  # noqa: BLE001 - classify dependency failure
        raise OcrError(
            "PyTorch is not installed; install the 'ocr' extra to enable TrOCR",
            category=DocumentErrorCategory.ocr_unavailable.value,
            permanent=True,
        ) from exc
    try:
        import transformers  # noqa: F401 - presence probe
    except Exception as exc:  # noqa: BLE001 - classify dependency failure
        raise OcrError(
            "Hugging Face Transformers is not installed; install the 'ocr' extra",
            category=DocumentErrorCategory.ocr_unavailable.value,
            permanent=True,
        ) from exc
    try:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    except Exception as exc:  # noqa: BLE001 - classify, never swallow
        # "transformers imports but not its TrOCR classes" has two very
        # different causes, and reporting the wrong one sends operators down the
        # wrong path, so probe the usual suspect directly. A vision model like
        # TrOCR pulls in torchvision through transformers' lazy loader; an
        # incompatible torchvision (one built for a newer torch) breaks that
        # loader with a confusing "cannot import name" that must NOT be reported
        # as "transformers not installed". The ImportError carries no exception
        # chain, so the only reliable signal is importing torchvision itself.
        try:
            import torchvision  # noqa: F401 - probe only
        except Exception as tv_exc:  # noqa: BLE001
            raise OcrError(
                "Transformers is installed but a transitive vision dependency "
                f"(torchvision) failed to import ({type(tv_exc).__name__}). This "
                "usually means torchvision does not match the installed torch; "
                "install the pinned 'ocr' extra so the versions align",
                category=DocumentErrorCategory.ocr_unavailable.value,
                permanent=True,
            ) from exc
        raise OcrError(
            "Transformers is installed but does not expose the TrOCR classes; "
            "it may be too old or too new for this adapter",
            category=DocumentErrorCategory.ocr_unavailable.value,
            permanent=True,
        ) from exc
    return torch, TrOCRProcessor, VisionEncoderDecoderModel


class TrocrOcrProvider(OcrProvider):
    """Genuine local TrOCR inference over detected text lines."""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        device: str = "auto",
        cache_dir: Optional[str] = None,
        local_files_only: bool = False,
    ) -> None:
        self.model_id = model_id
        self.requested_device = device
        self.cache_dir = cache_dir or None
        self.local_files_only = local_files_only

        self._lock = threading.Lock()
        self._torch: Any = None
        self._processor: Any = None
        self._model: Any = None
        self._device: Optional[str] = None
        self._load_seconds: Optional[float] = None

    @classmethod
    def from_settings(cls, settings) -> "TrocrOcrProvider":
        return cls(
            model_id=settings.ocr_model_id,
            device=settings.ocr_device,
            cache_dir=settings.ocr_model_cache_dir or None,
            local_files_only=settings.ocr_local_files_only,
        )

    # -- device resolution -------------------------------------------------

    def _resolve_device(self, torch: Any) -> str:
        """Resolve the requested device; never silently fall back."""
        cuda_available = bool(torch.cuda.is_available())
        if self.requested_device == "cuda":
            if not cuda_available:
                raise OcrError(
                    "OCR_DEVICE=cuda was requested but CUDA is not available",
                    category=DocumentErrorCategory.ocr_unavailable.value,
                    permanent=True,
                )
            return "cuda"
        if self.requested_device == "cpu":
            return "cpu"
        # auto
        return "cuda" if cuda_available else "cpu"

    # -- lazy loading ------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load model+processor once, under a lock, on first use."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            started = time.monotonic()
            torch, TrOCRProcessor, VisionEncoderDecoderModel = _import_ml()
            device = self._resolve_device(torch)
            common = {
                "cache_dir": self.cache_dir,
                "local_files_only": self.local_files_only,
            }
            try:
                processor = TrOCRProcessor.from_pretrained(self.model_id, **common)
                model = VisionEncoderDecoderModel.from_pretrained(self.model_id, **common)
            except Exception as exc:  # noqa: BLE001 - missing artifact / bad id
                raise OcrError(
                    "TrOCR model artifacts are unavailable or could not be loaded",
                    category=DocumentErrorCategory.ocr_unavailable.value,
                    permanent=True,
                ) from exc
            try:
                model.to(device)
                model.eval()
            except Exception as exc:  # noqa: BLE001 - device placement failure
                raise OcrError(
                    "TrOCR model could not be placed on the requested device",
                    category=DocumentErrorCategory.ocr_unavailable.value,
                    permanent=True,
                ) from exc

            self._torch = torch
            self._processor = processor
            self._model = model
            self._device = device
            self._load_seconds = time.monotonic() - started

    def close(self) -> None:
        """Release the model so memory can be reclaimed."""
        with self._lock:
            self._model = None
            self._processor = None
            if self._torch is not None and self._device == "cuda":
                try:
                    self._torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001 - best effort
                    pass
            self._torch = None
            self._device = None

    # -- inference ---------------------------------------------------------

    def _prepare_crop(self, image: Image.Image, limits: OcrLimits) -> Image.Image:
        """Bound a crop before it reaches the processor."""
        img = image.convert("RGB")
        longest = max(img.size)
        cap = min(limits.max_crop_dimension, _PROCESSOR_MAX_SIDE)
        if longest > cap:
            ratio = cap / float(longest)
            img = img.resize(
                (max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
                Image.Resampling.LANCZOS,
            )
        return img

    def _recognize_crop(self, crop: Image.Image) -> str:
        """Run real inference on one line crop and return decoded text."""
        torch = self._torch
        pixel_values = self._processor(images=crop, return_tensors="pt").pixel_values
        pixel_values = pixel_values.to(self._device)
        # inference_mode disables autograd and is cheaper than no_grad on CPU.
        with torch.inference_mode():
            generated_ids = self._model.generate(pixel_values)
        text = self._processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return text

    def recognize(self, page: OcrPageInput, *, limits: OcrLimits) -> OcrPageResult:
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

        self._ensure_loaded()

        deadline = time.monotonic() + limits.page_timeout_seconds
        boxes = extract_text_lines(
            image,
            min_ink_ratio=limits.min_line_ink_ratio,
            max_regions=limits.max_regions_per_page,
        )

        regions: list[OcrRegionOutput] = []
        warnings: list[str] = []
        dropped = 0
        timed_out = False

        for index, box in enumerate(boxes):
            if time.monotonic() > deadline:
                timed_out = True
                break
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
            try:
                crop_box = (box.left, box.top, box.left + box.width, box.top + box.height)
                crop = self._prepare_crop(image.crop(crop_box), limits)
                raw = self._recognize_crop(crop)
            except OcrError:
                raise
            except Exception as exc:  # noqa: BLE001 - inference failure
                name = type(exc).__name__
                if "out of memory" in str(exc).lower() or name == "OutOfMemoryError":
                    raise OcrError(
                        "OCR ran out of memory while recognizing a line",
                        category=DocumentErrorCategory.ocr_failed.value,
                        permanent=False,
                    ) from exc
                raise OcrError(
                    "TrOCR inference failed while recognizing a line",
                    category=DocumentErrorCategory.ocr_failed.value,
                    permanent=False,
                ) from exc

            text = normalize_ocr_text(raw)
            if is_effectively_empty(text):
                # A real empty prediction, not a failure: keep the region so
                # reviewers can see the line was read and produced nothing.
                warnings.append("empty_prediction")
            region = OcrRegionOutput(
                bbox_left=left,
                bbox_top=top,
                bbox_width=width,
                bbox_height=height,
                raw_text=raw,
                text=text,
                reading_order=index,
                confidence=None,
            )
            if not validate_region(region, page_width=page.width_px, page_height=page.height_px):
                dropped += 1
                continue
            regions.append(region)

        if dropped:
            warnings.append("regions_dropped_invalid_geometry")
        if timed_out:
            warnings.append("page_timeout_partial")

        empty_reason = None
        if not regions:
            empty_reason = "page_timeout_partial" if timed_out else "no_text_line_detected"

        return OcrPageResult(
            provider=PROVIDER_NAME,
            model_id=self.model_id,
            model_version=None,
            processing_version=OCR_PROCESSING_VERSION,
            regions=tuple(regions),
            duration_ms=watch.elapsed_ms,
            warnings=tuple(warnings),
            empty_reason=empty_reason,
            partial=timed_out,
        )

    def describe(self) -> dict:
        return {
            "provider": PROVIDER_NAME,
            "model_id": self.model_id,
            "requested_device": self.requested_device,
            "resolved_device": self._device,
            "local_files_only": self.local_files_only,
            "loaded": self._model is not None,
            "load_seconds": None if self._load_seconds is None else round(self._load_seconds, 3),
        }
