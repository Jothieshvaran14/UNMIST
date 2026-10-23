"""Bounded page preprocessing: image normalization and PDF rasterization.

This module turns an uploaded original into one or more normalized *working
page images*, ready for a later OCR stage (Checkpoint D). It performs **no**
text recognition: producing a page image is not the same as reading it.

Coordinate convention (documented, consistent across the pipeline):

  * The *original* page space is the EXIF-corrected image for image uploads,
    or the rasterized page at the configured render scale for PDFs.
    ``DocumentPage.width_px``/``height_px`` record this space.
  * The *working* image may be downscaled to fit the working-dimension limit.
    ``DocumentPage.working_scale`` is ``working / original`` (uniform).
  * OCR regions are always stored back in the **original** space, so a box
    drawn over the working image maps back as ``original = working / scale``.

Design properties:

  * Everything is memory-resident (``BytesIO``); the default pipeline creates
    no temp files, which removes a whole class of cleanup bugs. A per-attempt
    temp directory is still provided for backends that need disk.
  * Resource limits are enforced *before* and *during* work, not just noted.
  * Preprocessing is deliberately conservative: downscale-only, no binarize,
    no sharpen, no denoise. Whether any transform helps OCR is an evaluation
    question (Checkpoint E), not an assumption here.
  * Originals are never modified; working images are separate artifacts.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from ...config import Settings, get_settings

# Version tag stamped on every page so artifacts can be reprocessed when the
# pipeline's behavior changes. Bump when normalization semantics change.
PREPROCESSING_VERSION = "preprocess-v1"


class PreprocessError(Exception):
    """A preprocessing failure with a bounded, non-sensitive category.

    ``permanent`` distinguishes input errors that will never succeed on retry
    (malformed file, unsupported format) from transient infrastructure errors
    (storage down, out of memory) that are worth retrying.
    """

    def __init__(self, message: str, *, category: str = "preprocessing", permanent: bool = False):
        super().__init__(message)
        self.message = message
        self.category = category
        self.permanent = permanent


@dataclass(frozen=True)
class PageLimits:
    """Resolved, validated resource limits for one preprocessing job."""

    render_scale: float
    pdf_max_page_dimension: int
    max_working_dimension: int
    page_timeout_seconds: float
    job_timeout_seconds: float
    max_total_pages: int

    @classmethod
    def from_settings(cls, settings: Optional[Settings] = None) -> "PageLimits":
        cfg = settings or get_settings()
        return cls(
            render_scale=cfg.pdf_render_scale,
            pdf_max_page_dimension=cfg.pdf_max_page_dimension,
            max_working_dimension=cfg.preprocess_max_working_dimension,
            page_timeout_seconds=cfg.preprocess_page_timeout_seconds,
            job_timeout_seconds=cfg.preprocess_job_timeout_seconds,
            max_total_pages=cfg.preprocess_max_total_pages,
        )


@dataclass
class PreparedPage:
    """One preprocessed page, in working-image form.

    ``image_png`` is the normalized working image. ``original_width``/
    ``original_height`` describe the canonical original space that OCR boxes
    will be expressed in; ``working_scale`` maps working pixels to it.
    """

    page_number: int
    original_width: int
    original_height: int
    working_width: int
    working_height: int
    working_scale: float
    image_png: bytes
    content_hash: str
    backend: str
    warnings: list[str] = field(default_factory=list)


@contextmanager
def attempt_temp_dir(base: Optional[str] = None) -> Iterator[Path]:
    """Attempt-scoped temp directory, removed on success, failure, or crash.

    The default pipeline is memory-resident and does not need this, but any
    backend that spills to disk must use it so abandoned artifacts are cleaned
    deterministically. The directory is unique per attempt, so two concurrent
    attempts never collide.
    """
    parent = Path(base).expanduser() if base else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="unmist-pre-", dir=parent))
    try:
        yield tmp
    finally:
        # Best-effort recursive cleanup; never raises out of the pipeline.
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _downscale_size(width: int, height: int, max_dimension: int) -> tuple[int, int]:
    """Return a size that fits within ``max_dimension`` without upscaling."""
    longest = max(width, height)
    if longest <= max_dimension:
        return width, height
    ratio = max_dimension / float(longest)
    new_w = max(1, int(round(width * ratio)))
    new_h = max(1, int(round(height * ratio)))
    return new_w, new_h


def _encode_png(img) -> bytes:
    buf = io.BytesIO()
    # Optimize keeps artifacts small; the image is already normalized.
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _is_blank(img) -> bool:
    """Detect an obviously blank/near-blank page (all pixels ~identical)."""
    try:
        extrema = img.convert("L").getextrema()
    except Exception:  # noqa: BLE001 - extrema is advisory only
        return False
    if extrema is None:
        return True
    lo, hi = extrema
    return (hi - lo) <= 2


def normalize_image_bytes(
    data: bytes,
    *,
    max_working_dimension: int,
) -> tuple[bytes, int, int, int, int, float, list[str]]:
    """Normalize one image into a working PNG.

    Returns ``(png_bytes, original_w, original_h, working_w, working_h, scale,
    warnings)``. Applies EXIF orientation, converts to grayscale, and downscales
    to the working limit (never upscales). Refuses to invent geometry.
    """
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - dependency is required
        raise PreprocessError(
            "Pillow is required for image preprocessing", category="preprocessing", permanent=True
        ) from exc

    try:
        opened = Image.open(io.BytesIO(data))
        # EXIF transpose first so "original" means the orientation a human sees.
        img: Image.Image = ImageOps.exif_transpose(opened) or opened
    except (UnidentifiedImageError, OSError, ValueError):
        raise PreprocessError(
            "Image could not be decoded", category="validation", permanent=True
        ) from None

    warnings: list[str] = []
    original_w, original_h = img.size
    if original_w < 1 or original_h < 1:
        raise PreprocessError(
            "Image has invalid dimensions", category="validation", permanent=True
        )

    working_w, working_h = _downscale_size(original_w, original_h, max_working_dimension)
    if (working_w, working_h) != (original_w, original_h):
        # LANCZOS gives a stable, high-quality downscale.
        img = img.resize((working_w, working_h), Image.Resampling.LANCZOS)
        warnings.append("downscaled_to_working_limit")

    # Normalize to a single-channel grayscale representation for OCR.
    img = img.convert("L")
    if _is_blank(img):
        warnings.append("possibly_blank")

    png = _encode_png(img)
    scale = working_w / float(original_w)
    return png, original_w, original_h, working_w, working_h, scale, warnings


def _pypdfium2_available() -> bool:
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return False
    return True


def rasterize_pdf(
    data: bytes,
    *,
    limits: PageLimits,
    deadline: float,
) -> list[PreparedPage]:
    """Rasterize a PDF into working page images via the configured backend.

    Raises a clear, permanent error when the rasterization backend is not
    installed, so an unavailable backend is an explicit capability boundary
    rather than a silent wrong result. Page count and dimensions are bounded;
    the per-page and whole-job time budgets are enforced between pages.
    """
    settings = get_settings()
    if settings.pdf_rasterizer == "none" or not _pypdfium2_available():
        raise PreprocessError(
            "PDF rasterization backend is not available; install pypdfium2 or "
            "configure PDF_RASTERIZER",
            category="preprocessing",
            permanent=True,
        )

    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
        page_count = len(pdf)
    except Exception:  # noqa: BLE001 - pypdfium2 raises library-specific errors
        raise PreprocessError(
            "PDF could not be opened for rendering", category="validation", permanent=True
        ) from None

    if page_count < 1:
        raise PreprocessError(
            "PDF contains no pages", category="validation", permanent=True
        )
    if page_count > limits.max_total_pages:
        raise PreprocessError(
            f"PDF exceeds the {limits.max_total_pages}-page processing limit",
            category="validation",
            permanent=True,
        )

    pages: list[PreparedPage] = []
    for index in range(page_count):
        if time.monotonic() > deadline:
            raise PreprocessError(
                "Preprocessing exceeded the job time budget",
                category="preprocessing",
                permanent=False,
            )
        page_start = time.monotonic()
        try:
            bitmap = pdf[index].render(scale=limits.render_scale)
            img = bitmap.to_pil()
        except Exception:  # noqa: BLE001
            raise PreprocessError(
                f"Page {index + 1} could not be rendered", category="preprocessing", permanent=False
            ) from None

        if time.monotonic() - page_start > limits.page_timeout_seconds:
            raise PreprocessError(
                f"Page {index + 1} exceeded the per-page time budget",
                category="preprocessing",
                permanent=False,
            )

        warnings: list[str] = []
        original_w, original_h = img.size
        longest = max(original_w, original_h)
        if longest > limits.pdf_max_page_dimension:
            # Re-render at a reduced scale rather than shipping an oversized page.
            clamp_ratio = limits.pdf_max_page_dimension / float(longest)
            clamped_scale = max(0.1, limits.render_scale * clamp_ratio)
            try:
                img = pdf[index].render(scale=clamped_scale).to_pil()
                warnings.append("clamped_to_max_page_dimension")
            except Exception:  # noqa: BLE001
                raise PreprocessError(
                    f"Page {index + 1} could not be rendered",
                    category="preprocessing",
                    permanent=False,
                ) from None
            original_w, original_h = img.size

        working_w, working_h = _downscale_size(
            original_w, original_h, limits.max_working_dimension
        )
        if (working_w, working_h) != (original_w, original_h):
            from PIL import Image as _PILImage

            img = img.resize((working_w, working_h), _PILImage.Resampling.LANCZOS)
            warnings.append("downscaled_to_working_limit")

        img = img.convert("L")
        if _is_blank(img):
            warnings.append("possibly_blank")

        png = _encode_png(img)
        pages.append(
            PreparedPage(
                page_number=index + 1,
                original_width=original_w,
                original_height=original_h,
                working_width=working_w,
                working_height=working_h,
                working_scale=working_w / float(original_w),
                image_png=png,
                content_hash=hashlib.sha256(png).hexdigest(),
                backend="pypdfium2",
                warnings=warnings,
            )
        )
    return pages


def preprocess_image(data: bytes, *, limits: PageLimits) -> list[PreparedPage]:
    """Preprocess a single-image document into exactly one working page."""
    png, orig_w, orig_h, work_w, work_h, scale, warnings = normalize_image_bytes(
        data, max_working_dimension=limits.max_working_dimension
    )
    return [
        PreparedPage(
            page_number=1,
            original_width=orig_w,
            original_height=orig_h,
            working_width=work_w,
            working_height=work_h,
            working_scale=scale,
            image_png=png,
            content_hash=hashlib.sha256(png).hexdigest(),
            backend="pillow",
            warnings=warnings,
        )
    ]


def preprocess_document(
    data: bytes,
    *,
    media_type: str,
    limits: PageLimits,
    deadline: Optional[float] = None,
) -> list[PreparedPage]:
    """Dispatch to the right preprocessor for a validated original.

    ``media_type`` is the *detected* type recorded at upload, never the
    client-supplied one. The job-level time budget is threaded through so a
    runaway multi-page PDF cannot hold a worker indefinitely.
    """
    deadline = deadline if deadline is not None else time.monotonic() + limits.job_timeout_seconds
    if media_type == "application/pdf":
        return rasterize_pdf(data, limits=limits, deadline=deadline)
    if media_type in {"image/png", "image/jpeg", "image/tiff"}:
        if time.monotonic() > deadline:
            raise PreprocessError(
                "Preprocessing exceeded the job time budget",
                category="preprocessing",
                permanent=False,
            )
        return preprocess_image(data, limits=limits)
    raise PreprocessError(
        f"Unsupported media type for preprocessing: {media_type}",
        category="validation",
        permanent=True,
    )
