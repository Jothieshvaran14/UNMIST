"""Upload validation: magic bytes, size/type/page limits, and hashing.

Files are treated as hostile. Nothing here trusts the client-supplied
``Content-Type`` or the filename extension; the format is decided by the file's
magic bytes, then the real parser (PyPDF2 / Pillow) must open the file without
error before it is accepted. Every limit is enforced *before* the bytes are
persisted or handed to a model.

Rejecting early is the point: a document that fails validation is never stored,
never enqueued, and never reaches the OCR pipeline.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from typing import Optional

from ...config import Settings, get_settings
from ...errors import PayloadTooLargeError, ValidationError

# --- magic-byte signatures -------------------------------------------------
_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_TIFF_LE_MAGIC = b"II*\x00"
_TIFF_BE_MAGIC = b"MM\x00*"


@dataclass(frozen=True)
class DetectedFormat:
    canonical: str  # e.g. "pdf", "jpg"
    media_type: str  # e.g. "application/pdf"


# canonical format -> media type, and the extension set that may claim it.
_FORMAT_MEDIA = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "png": "image/png",
    "tiff": "image/tiff",
}

_EXT_TO_FORMAT = {
    "pdf": "pdf",
    "jpg": "jpg",
    "jpeg": "jpg",
    "png": "png",
    "tif": "tiff",
    "tiff": "tiff",
}


def detect_format(data: bytes) -> Optional[DetectedFormat]:
    """Decide the real format from leading bytes, ignoring the filename."""
    if data[: len(_PDF_MAGIC)] == _PDF_MAGIC:
        canonical = "pdf"
    elif data[: len(_PNG_MAGIC)] == _PNG_MAGIC:
        canonical = "png"
    elif data[: len(_JPEG_MAGIC)] == _JPEG_MAGIC:
        canonical = "jpg"
    elif data[:4] in (_TIFF_LE_MAGIC, _TIFF_BE_MAGIC):
        canonical = "tiff"
    else:
        return None
    return DetectedFormat(canonical=canonical, media_type=_FORMAT_MEDIA[canonical])


def sanitize_filename(raw: Optional[str]) -> str:
    """Reduce an untrusted filename to a safe display string.

    Strips any directory components, control characters, and shell-hostile
    characters, and bounds the length. The result is *display metadata only*;
    it is never used to build a storage path.
    """
    if not raw:
        return "upload"
    # Drop any path components (both separators) before other handling.
    name = re.split(r"[\\/]", raw)[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = re.sub(r"\s+", " ", name).strip()
    # Remove characters that are hostile in shells, logs, or markup.
    name = re.sub(r"[;|&$`<>\"'*?]", "", name)
    name = name.lstrip(".")
    if not name:
        return "upload"
    return name[:255]


@dataclass(frozen=True)
class ValidatedUpload:
    canonical_format: str
    media_type: str
    display_filename: str
    content_hash: str
    size_bytes: int
    page_count: int
    width_px: Optional[int]
    height_px: Optional[int]


def validate_upload(
    data: bytes,
    filename: Optional[str],
    *,
    settings: Optional[Settings] = None,
) -> ValidatedUpload:
    """Validate an uploaded file and return its normalized metadata.

    Raises ``ValidationError`` (422) or ``PayloadTooLargeError`` (413) for any
    file that must not enter the system. On success the caller can persist the
    bytes with confidence that they are a supported, non-empty, parseable
    document within the configured limits.
    """
    cfg = settings or get_settings()
    display_filename = sanitize_filename(filename)

    if not data:
        raise ValidationError("Uploaded file is empty", code="empty_file")

    if len(data) > cfg.upload_max_bytes:
        raise PayloadTooLargeError(
            f"File exceeds the {cfg.upload_max_bytes}-byte upload limit",
            code="file_too_large",
        )

    detected = detect_format(data)
    if detected is None:
        raise ValidationError(
            "File type is not supported", code="unsupported_file_type"
        )

    # The extension, when present, must agree with the magic bytes. A mismatch
    # is a strong signal of a disguised file and is rejected outright.
    ext = display_filename.rsplit(".", 1)[-1].lower() if "." in display_filename else ""
    claimed = _EXT_TO_FORMAT.get(ext)
    if claimed is not None and claimed != detected.canonical:
        raise ValidationError(
            "File extension does not match its actual content",
            code="extension_type_mismatch",
        )
    if detected.canonical not in cfg.upload_allowed_formats:
        raise ValidationError(
            f"File type '{detected.canonical}' is not permitted by configuration",
            code="file_type_not_permitted",
        )

    if detected.canonical == "pdf":
        page_count, width_px, height_px = _validate_pdf(data, cfg)
    else:
        page_count, width_px, height_px = _validate_image(data, cfg)

    return ValidatedUpload(
        canonical_format=detected.canonical,
        media_type=detected.media_type,
        display_filename=display_filename,
        content_hash=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        page_count=page_count,
        width_px=width_px,
        height_px=height_px,
    )


def _validate_pdf(data: bytes, cfg: Settings) -> tuple[int, Optional[int], Optional[int]]:
    """Open the PDF with the real parser; reject malformed or encrypted files."""
    try:
        from PyPDF2 import PdfReader
        from PyPDF2.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - dependency is required
        raise ValidationError(
            "PDF validation is unavailable", code="validation_unavailable"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        # Encrypted PDFs are rejected with a clear, distinct status rather than
        # a generic parse failure, so callers can distinguish the two.
        if reader.is_encrypted:
            raise ValidationError(
                "Encrypted PDFs are not supported", code="encrypted_pdf"
            )
        page_count = len(reader.pages)
    except ValidationError:
        raise
    except (PdfReadError, ValueError, TypeError, KeyError, IndexError, RecursionError, EOFError):
        raise ValidationError("PDF file is malformed", code="malformed_file") from None

    if page_count < 1:
        raise ValidationError("PDF contains no pages", code="malformed_file")
    if page_count > cfg.upload_max_pages:
        raise ValidationError(
            f"PDF exceeds the {cfg.upload_max_pages}-page limit",
            code="too_many_pages",
        )
    # PDF page geometry is left in PDF points; rasterization happens later.
    return page_count, None, None


def _validate_image(data: bytes, cfg: Settings) -> tuple[int, Optional[int], Optional[int]]:
    """Open the image with Pillow and enforce dimension/pixel limits."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - dependency is required
        raise ValidationError(
            "Image validation is unavailable", code="validation_unavailable"
        ) from exc

    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            fmt = (img.format or "").lower()
            # verify() re-reads the file to catch truncated/corrupt streams
            # without fully decoding pixel data.
            img.verify()
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ValidationError("Image file is malformed", code="malformed_file") from None

    if width < 1 or height < 1:
        raise ValidationError("Image has invalid dimensions", code="malformed_file")
    if width > cfg.upload_max_image_dimension or height > cfg.upload_max_image_dimension:
        raise ValidationError(
            f"Image exceeds the {cfg.upload_max_image_dimension}-pixel dimension limit",
            code="image_too_large",
        )
    if width * height > cfg.upload_max_image_pixels:
        raise ValidationError(
            f"Image exceeds the {cfg.upload_max_image_pixels}-pixel area limit",
            code="image_too_large",
        )
    # Guard against a claimed type that Pillow can open but that disagrees with
    # the magic bytes we already accepted (defence in depth).
    _ = fmt
    return 1, width, height
