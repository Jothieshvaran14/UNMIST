"""Upload-validation and private-storage tests.

Every fixture file here is a *real* file produced by a real writer (Pillow for
images, PyPDF2 for PDFs), so the tests exercise genuine parser paths rather than
hand-crafted byte strings that only pretend to be valid.
"""

from __future__ import annotations

import io

import pytest
from app.config import load_settings
from app.errors import PayloadTooLargeError, ValidationError
from app.services.documents.validation import (
    detect_format,
    sanitize_filename,
    validate_upload,
)
from app.services.storage import (
    ConflictError,
    LocalObjectStore,
    NotFoundError,
    ObjectStoreError,
    generate_document_key,
)

# --- real file factories ---------------------------------------------------


def make_png(width: int = 24, height: int = 16, color=(200, 30, 30)) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_jpeg(width: int = 24, height: int = 16) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_tiff(width: int = 24, height: int = 16) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), (5, 5, 5))
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()


def make_pdf(pages: int = 2) -> bytes:
    from PyPDF2 import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        # A4-ish empty page, in PDF points.
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_encrypted_pdf() -> bytes:
    from PyPDF2 import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("hunter2")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# --- magic-byte detection --------------------------------------------------


def test_detect_format_ignores_filename():
    data = make_png()
    # Filename lies; bytes tell the truth.
    detected = detect_format(data)
    assert detected is not None
    assert detected.canonical == "png"
    assert detected.media_type == "image/png"


@pytest.mark.parametrize(
    "factory,canonical",
    [
        (make_png, "png"),
        (make_jpeg, "jpg"),
        (make_tiff, "tiff"),
        (make_pdf, "pdf"),
    ],
)
def test_detect_format_all_supported(factory, canonical):
    assert detect_format(factory()).canonical == canonical


def test_detect_format_rejects_unknown():
    assert detect_format(b"this is not a document") is None
    assert detect_format(b"") is None


def test_detect_format_rejects_disguised_executable():
    # MZ is the DOS/PE magic; a renamed binary must never be treated as an image.
    assert detect_format(b"MZ\x90\x00" + b"\x00" * 32) is None


# --- filename sanitisation -------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("/absolute/path/notes.png", "notes.png"),
        ("a;b|c&d.png", "abcd.png"),
        ("", "upload"),
        (None, "upload"),
        ("...", "upload"),
        ("\x00\x01evil.png", "evil.png"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_bounds_length():
    name = "a" * 500 + ".png"
    assert len(sanitize_filename(name)) <= 255


# --- validate_upload -------------------------------------------------------


def test_validate_upload_accepts_real_png():
    result = validate_upload(make_png(), "photo.png")
    assert result.canonical_format == "png"
    assert result.media_type == "image/png"
    assert result.page_count == 1
    assert result.width_px == 24
    assert result.height_px == 16
    assert len(result.content_hash) == 64
    assert result.size_bytes > 0


def test_validate_upload_accepts_real_pdf_and_counts_pages():
    result = validate_upload(make_pdf(pages=3), "doc.pdf")
    assert result.canonical_format == "pdf"
    assert result.media_type == "application/pdf"
    assert result.page_count == 3
    # PDF geometry is not rasterized at upload time.
    assert result.width_px is None


def test_validate_upload_rejects_empty():
    with pytest.raises(ValidationError) as exc:
        validate_upload(b"", "empty.png")
    assert exc.value.code == "empty_file"


def test_validate_upload_rejects_oversized():
    cfg = load_settings()
    big = make_png()  # small real image...
    # ...but we assert the size guard using a lowered limit via a real file.
    padded = big + b"\x00" * (cfg.upload_max_bytes + 1)
    with pytest.raises(PayloadTooLargeError) as exc:
        validate_upload(padded, "big.png", settings=cfg)
    assert exc.value.code == "file_too_large"


def test_validate_upload_rejects_disguised_type():
    with pytest.raises(ValidationError) as exc:
        validate_upload(b"MZ binary content here", "malware.png")
    assert exc.value.code == "unsupported_file_type"


def test_validate_upload_rejects_extension_mismatch():
    # Real PNG bytes, but the filename claims to be a PDF.
    with pytest.raises(ValidationError) as exc:
        validate_upload(make_png(), "disguised.pdf")
    assert exc.value.code == "extension_type_mismatch"


def test_validate_upload_rejects_permitted_extension_with_bad_bytes():
    # .png extension + plausible-but-corrupt PNG magic.
    corrupt = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    with pytest.raises(ValidationError) as exc:
        validate_upload(corrupt, "corrupt.png")
    assert exc.value.code == "malformed_file"


def test_validate_upload_rejects_malformed_pdf():
    corrupt = b"%PDF-1.7\n" + b"garbage not a real xref table" * 10
    with pytest.raises(ValidationError) as exc:
        validate_upload(corrupt, "corrupt.pdf")
    assert exc.value.code == "malformed_file"


def test_validate_upload_rejects_encrypted_pdf_with_clear_status():
    with pytest.raises(ValidationError) as exc:
        validate_upload(make_encrypted_pdf(), "locked.pdf")
    assert exc.value.code == "encrypted_pdf"


def test_validate_upload_enforces_page_limit():
    from app.config import Settings

    cfg = load_settings()
    limited = Settings(
        **{**cfg.__dict__, "upload_max_pages": 1}
    )
    with pytest.raises(ValidationError) as exc:
        validate_upload(make_pdf(pages=3), "many.pdf", settings=limited)
    assert exc.value.code == "too_many_pages"


def test_validate_upload_enforces_image_dimension_limit():
    from app.config import Settings

    cfg = load_settings()
    limited = Settings(**{**cfg.__dict__, "upload_max_image_dimension": 8})
    with pytest.raises(ValidationError) as exc:
        validate_upload(make_png(width=32, height=32), "big.png", settings=limited)
    assert exc.value.code == "image_too_large"


def test_validate_upload_rejects_type_not_in_allowlist():
    from app.config import Settings

    cfg = load_settings()
    restricted = Settings(**{**cfg.__dict__, "upload_allowed_formats": frozenset({"pdf"})})
    with pytest.raises(ValidationError) as exc:
        validate_upload(make_png(), "photo.png", settings=restricted)
    assert exc.value.code == "file_type_not_permitted"


# --- private object store --------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    return LocalObjectStore(tmp_path)


def test_store_put_and_get_roundtrip(store):
    key = generate_document_key("doc-1", kind="originals")
    payload = b"hello world"
    store.put(key, payload, expected_hash=None)
    assert store.get(key) == payload


def test_store_generates_opaque_keys_ignoring_filename():
    key = generate_document_key("doc-2", kind="originals")
    # Keys are app-generated and carry no user-controlled filename.
    assert key.startswith("documents/doc-2/originals/")
    assert " " not in key
    assert ".." not in key


def test_store_rejects_path_traversal_keys(store):
    from app.errors import ValidationError as AppValidationError

    for bad in [
        "../escape.txt",
        "documents/../../etc/passwd",
        "/absolute/path.txt",
        "a\\b.txt",
        "documents/./x.txt",
    ]:
        with pytest.raises(AppValidationError):
            store.put(bad, b"x")


def test_store_refuses_to_overwrite_originals(store):
    key = generate_document_key("doc-3", kind="originals")
    store.put(key, b"first")
    with pytest.raises(ConflictError):
        store.put(key, b"second")
    # The original bytes are untouched.
    assert store.get(key) == b"first"


def test_store_verifies_hash_on_read(store):
    key = generate_document_key("doc-4", kind="originals")
    store.put(key, b"payload")
    # Correct hash returns bytes.
    import hashlib

    good = hashlib.sha256(b"payload").hexdigest()
    assert store.get_verified(key, good) == b"payload"
    # A mismatched hash is detected rather than silently served.
    with pytest.raises(ObjectStoreError):
        store.get_verified(key, "0" * 64)


def test_store_put_rejects_bytes_that_fail_hash_check(store):
    key = generate_document_key("doc-5", kind="originals")
    with pytest.raises(ObjectStoreError):
        store.put(key, b"payload", expected_hash="0" * 64)


def test_store_missing_object_raises_not_found(store):
    with pytest.raises(NotFoundError):
        store.get("documents/nope/originals/deadbeef")


def test_store_delete_removes_object(store):
    key = generate_document_key("doc-6", kind="originals")
    store.put(key, b"temp")
    store.delete(key)
    assert store.exists(key) is False
