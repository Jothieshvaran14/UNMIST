"""Document ingestion API tests: authorization, tenancy, and persistence.

These are integration tests over the real HTTP surface. They verify that an
authorized upload is validated, stored privately, persisted, and scheduled; that
unauthorized and cross-tenant access is refused; and that nothing about the
original file is exposed through the API.
"""

from __future__ import annotations

import io

import pytest
from app.models.documents import DocumentProcessingState
from app.models.job import Job
from app.models.knowledge import SourceDocument
from app.services.documents.state import (
    assert_transition,
    can_transition,
    mark_completed,
    parse_state,
)

# --- helpers ---------------------------------------------------------------


def _png_bytes(width: int = 20, height: int = 20) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), (12, 200, 90))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf_bytes(pages: int = 2) -> bytes:
    from PyPDF2 import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture()
def storage_dir(tmp_path, monkeypatch):
    """Point the app's private object store at a temp directory for the test."""
    import app.config as app_config
    import app.services.storage as storage

    target = tmp_path / "storage"
    # Settings is frozen, so redirect via env + singleton reset.
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(target))
    app_config.reset_settings_for_tests()
    storage.reset_object_store_for_tests()
    yield target
    storage.reset_object_store_for_tests()
    app_config.reset_settings_for_tests()


@pytest.fixture()
def second_user(client):
    """Register a second, distinct user for cross-tenant/IDOR tests."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "mallory@example.com", "password": "another-strong-pass"},
    )
    assert response.status_code == 201, response.text
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "mallory@example.com", "password": "another-strong-pass"},
    )
    assert login.status_code == 200, login.text
    return {"user": response.json(), "tokens": login.json()}


def _upload(client, headers, data: bytes, filename: str, org_id=None):
    files = {"file": (filename, data, "application/octet-stream")}
    form = {}
    if org_id:
        form["org_id"] = org_id
    return client.post(
        "/api/v1/documents/uploads", headers=headers, files=files, data=form
    )


# --- upload happy path -----------------------------------------------------


def test_upload_png_is_persisted_and_scheduled(client, auth_headers, storage_dir):
    resp = _upload(client, auth_headers, _png_bytes(), "notes.png")
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["processing_state"] == DocumentProcessingState.uploaded.value
    assert body["media_type"] == "image/png"
    assert body["page_count"] == 1
    assert body["size_bytes"] > 0
    assert len(body["content_hash"]) == 64
    assert body["original_filename"] == "notes.png"
    # The API never exposes the private storage location.
    assert "storage_key" not in body
    assert "url" not in body

    # A real file was written under the private root, with an app-generated key.
    files = list(storage_dir.rglob("*"))
    stored = [f for f in files if f.is_file()]
    assert len(stored) == 1
    # The user's filename never becomes part of the storage path.
    assert "notes.png" not in stored[0].name


def test_upload_schedules_durable_job(client, auth_headers, db_session, storage_dir):
    resp = _upload(client, auth_headers, _png_bytes(), "notes.png")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["last_job_id"]

    job = db_session.get(Job, body["last_job_id"])
    assert job is not None
    assert job.job_type == "document.process"
    assert job.status == "pending"
    assert job.payload["document_id"] == body["id"]
    # Idempotency key is derived from the document, so re-runs cannot duplicate.
    assert job.idempotency_key == f"document.process:{body['id']}"


def test_upload_pdf_records_page_count(client, auth_headers, storage_dir):
    resp = _upload(client, auth_headers, _pdf_bytes(pages=3), "deck.pdf")
    assert resp.status_code == 201, resp.text
    assert resp.json()["page_count"] == 3
    assert resp.json()["media_type"] == "application/pdf"


def test_duplicate_upload_returns_existing_document(
    client, auth_headers, db_session, storage_dir
):
    data = _png_bytes()
    first = _upload(client, auth_headers, data, "same.png")
    second = _upload(client, auth_headers, data, "same.png")
    assert first.status_code == 201
    assert second.status_code == 201
    # Same content hash, same owner -> the same document, no second copy.
    assert first.json()["id"] == second.json()["id"]
    stored = [f for f in storage_dir.rglob("*") if f.is_file()]
    assert len(stored) == 1
    docs = db_session.query(SourceDocument).all()
    assert len(docs) == 1


# --- upload rejection ------------------------------------------------------


def test_upload_requires_authentication(client, storage_dir):
    resp = _upload(client, {}, _png_bytes(), "notes.png")
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "filename,data,expected_code",
    [
        ("empty.png", b"", "empty_file"),
        ("fake.png", b"MZ not really a png", "unsupported_file_type"),
        ("liar.pdf", _png_bytes(), "extension_type_mismatch"),
    ],
)
def test_upload_rejects_invalid_files(
    client, auth_headers, storage_dir, filename, data, expected_code
):
    resp = _upload(client, auth_headers, data, filename)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == expected_code
    # Nothing was persisted for a rejected upload.
    assert not [f for f in storage_dir.rglob("*") if f.is_file()]


def test_upload_rejects_encrypted_pdf_with_clear_status(
    client, auth_headers, storage_dir
):
    from PyPDF2 import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("secret")
    buf = io.BytesIO()
    writer.write(buf)

    resp = _upload(client, auth_headers, buf.getvalue(), "locked.pdf")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "encrypted_pdf"


def test_upload_sanitizes_path_traversal_filename(client, auth_headers, storage_dir):
    resp = _upload(client, auth_headers, _png_bytes(), "../../../etc/passwd.png")
    assert resp.status_code == 201, resp.text
    # Display metadata is sanitised; the stored path is app-generated.
    assert resp.json()["original_filename"] == "passwd.png"
    stored = [f for f in storage_dir.rglob("*") if f.is_file()]
    assert len(stored) == 1
    assert "passwd.png" not in stored[0].name
    # And nothing escaped the storage root.
    assert storage_dir.resolve() in stored[0].resolve().parents


# --- authorization and tenancy (IDOR) --------------------------------------


def test_cannot_read_another_users_document(client, auth_headers, second_user, storage_dir):
    created = _upload(client, auth_headers, _png_bytes(), "mine.png")
    assert created.status_code == 201
    doc_id = created.json()["id"]

    other_headers = {"Authorization": f"Bearer {second_user['tokens']['access_token']}"}
    # Direct fetch by id must not leak another user's document.
    resp = client.get(f"/api/v1/documents/{doc_id}", headers=other_headers)
    assert resp.status_code == 404

    # Nor should it appear in the other user's listing.
    listing = client.get("/api/v1/documents", headers=other_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


def test_document_listing_is_scoped_to_owner(client, auth_headers, second_user, storage_dir):
    _upload(client, auth_headers, _png_bytes(), "a.png")
    _upload(client, auth_headers, _png_bytes(width=21), "b.png")

    mine = client.get("/api/v1/documents", headers=auth_headers)
    assert mine.status_code == 200
    assert mine.json()["total"] == 2

    other_headers = {"Authorization": f"Bearer {second_user['tokens']['access_token']}"}
    theirs = client.get("/api/v1/documents", headers=other_headers)
    assert theirs.json()["total"] == 0


def test_org_upload_rejects_non_member(client, auth_headers, second_user, storage_dir):
    org = client.post(
        "/api/v1/orgs", headers=auth_headers, json={"name": "Test Org"}
    )
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]

    other_headers = {"Authorization": f"Bearer {second_user['tokens']['access_token']}"}
    resp = _upload(client, other_headers, _png_bytes(), "x.png", org_id=org_id)
    assert resp.status_code == 422


def test_org_upload_succeeds_for_member(client, auth_headers, storage_dir):
    org = client.post(
        "/api/v1/orgs", headers=auth_headers, json={"name": "Org Two"}
    )
    assert org.status_code == 201
    org_id = org.json()["id"]

    resp = _upload(client, auth_headers, _png_bytes(), "x.png", org_id=org_id)
    assert resp.status_code == 201, resp.text
    assert resp.json()["org_id"] == org_id


# --- state machine ---------------------------------------------------------


def test_state_machine_allows_the_documented_lifecycle():
    path = [
        DocumentProcessingState.uploaded,
        DocumentProcessingState.queued,
        DocumentProcessingState.preprocessing,
        DocumentProcessingState.ocr_processing,
        DocumentProcessingState.review_required,
        DocumentProcessingState.completed,
        DocumentProcessingState.deletion_pending,
        DocumentProcessingState.deleted,
    ]
    for current, target in zip(path, path[1:]):
        assert can_transition(current, target), f"{current} -> {target}"


def test_state_machine_rejects_illegal_jumps():
    # No skipping straight to completed from an early state. (ocr_processing ->
    # completed is allowed only via mark_completed(), which enforces that every
    # page actually processed; the raw graph permits it for that guarded path.)
    for current in [
        DocumentProcessingState.uploaded,
        DocumentProcessingState.queued,
        DocumentProcessingState.preprocessing,
    ]:
        assert not can_transition(current, DocumentProcessingState.completed)


def test_completed_is_terminal_for_processing():
    assert not can_transition(
        DocumentProcessingState.completed, DocumentProcessingState.queued
    )
    assert not can_transition(
        DocumentProcessingState.completed, DocumentProcessingState.ocr_processing
    )
    assert can_transition(
        DocumentProcessingState.completed, DocumentProcessingState.deletion_pending
    )


def test_failed_can_be_retried_through_queued():
    assert can_transition(DocumentProcessingState.failed, DocumentProcessingState.queued)
    # But a retry must not jump straight back to completed.
    assert not can_transition(DocumentProcessingState.failed, DocumentProcessingState.completed)


def test_assert_transition_raises_conflict():
    from app.errors import ConflictError

    with pytest.raises(ConflictError) as exc:
        assert_transition(DocumentProcessingState.uploaded, DocumentProcessingState.completed)
    assert exc.value.code == "illegal_state_transition"


def test_partial_completion_never_becomes_completed():
    # The single choke point: fewer processed pages than total -> review_required.
    assert (
        mark_completed(
            DocumentProcessingState.ocr_processing, total_pages=4, processed_pages=3
        )
        == DocumentProcessingState.review_required
    )
    # Only a full pass may complete.
    assert (
        mark_completed(
            DocumentProcessingState.ocr_processing, total_pages=4, processed_pages=4
        )
        == DocumentProcessingState.completed
    )


def test_parse_state_rejects_unknown_values():
    from app.errors import ConflictError

    assert parse_state("queued") == DocumentProcessingState.queued
    with pytest.raises(ConflictError):
        parse_state("made_up_state")
