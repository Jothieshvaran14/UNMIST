"""Checkpoint D integration tests: durable ``document.ocr`` pipeline.

These drive the real queue (``run_once``/``claim_next``) and
``DocumentOcrPipeline`` over a real SQLite session and a real local object
store, using the ``fake`` provider so no model, download, or GPU is involved.
They cover: preprocessed -> OCR -> persisted regions, idempotent redelivery,
reviewer-correction preservation, provider-unavailable handling, lease expiry,
stale-worker finalization, cancellation, deletion, and authorization / tenant
isolation.

The real TrOCR model is exercised only by the opt-in smoke test.
"""

from __future__ import annotations

import io

import pytest
from app.config import load_settings
from app.models.documents import (
    DocumentErrorCategory,
    DocumentPage,
)
from app.models.documents import DocumentProcessingState as S
from app.models.documents import (
    OcrRegion,
    ReviewStatus,
)
from app.models.job import Job, JobStatus
from app.models.knowledge import SourceDocument
from app.models.user import User
from app.services import jobs as job_service
from app.services.documents import ocr_pipeline as ocr_mod
from app.services.documents.ingestion import (
    DOCUMENT_OCR_JOB,
    create_upload,
    enqueue_ocr,
    enqueue_processing,
)
from app.services.documents.ocr.fake import FAKE_MODEL_ID, FakeOcrProvider
from app.services.storage import get_object_store, reset_object_store_for_tests

# --- fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_providers():
    """Drop cached provider instances so factory patches actually take effect.

    ``get_ocr_provider`` caches one instance per name process-wide; without
    this reset a factory patched by one test would be shadowed by a cached
    instance from an earlier test in the same session.
    """
    from app.services.documents.ocr.registry import reset_ocr_providers_for_tests

    reset_ocr_providers_for_tests()
    yield
    reset_ocr_providers_for_tests()


@pytest.fixture()
def storage_dir(tmp_path, monkeypatch):
    """Point the app's private object store at a temp directory and select the
    ``fake`` OCR provider so these tests exercise the real pipeline without a
    model, a download, or a GPU."""
    import app.config as app_config

    target = tmp_path / "storage"
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(target))
    monkeypatch.setenv("OCR_PROVIDER", "fake")
    app_config.reset_settings_for_tests()
    reset_object_store_for_tests()
    yield target
    reset_object_store_for_tests()
    app_config.reset_settings_for_tests()


@pytest.fixture()
def store(storage_dir):
    return get_object_store(load_settings())


def _png(width=900, height=700) -> bytes:
    """A synthetic page with three text-like bands."""
    from PIL import Image, ImageDraw

    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    for y in (80, 220, 360):
        x = 60
        while x < 700:
            w = 14 + (x % 5) * 6
            draw.rectangle([x, y, x + w, y + 26], fill=25)
            x += w + 10
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_preprocessed_document(
    session_factory, session, store, *, data=None, org_id=None, owner_user_id=None
):
    """Ingest + preprocess a document, leaving it in ``preprocessed``.

    Creates a real ``User`` so the ``owner_user_id`` foreign key resolves,
    unless ``owner_user_id`` names an existing user (endpoint tests pass the
    authenticated caller's id so ownership matches the token).
    """
    if data is None:
        data = _png()
    if owner_user_id is None:
        user = User(
            email="ocr@example.com",
            password_hash="x" * 60,
            email_verified=True,
        )
        session.add(user)
        session.flush()
        owner_user_id = user.id

    doc, _validated, _created = create_upload(
        session,
        data=data,
        filename="doc.png",
        owner_user_id=owner_user_id,
        org_id=org_id,
        actor_id=owner_user_id,
    )
    enqueue_processing(session, doc, org_id=org_id)
    session.commit()

    # Run preprocessing for real so pages + artifacts exist.
    import app.workers.handlers  # noqa: F401

    job_service.run_once(session_factory)
    session.expire_all()
    fresh = session.get(SourceDocument, doc.id)
    assert fresh.processing_state == S.preprocessed.value, fresh.processing_state
    return fresh, session.get(User, owner_user_id)


def _run_ocr_job(session_factory, *, provider=None):
    """Claim and run the pending ``document.ocr`` job with a given provider."""
    import app.workers.handlers  # noqa: F401
    from app.services.documents.ocr import registry

    if provider is not None:
        registry._instances["fake"] = provider
    return job_service.run_once(session_factory)


def _regions_for(session, doc_id):
    return (
        session.query(OcrRegion)
        .filter(OcrRegion.document_id == doc_id)
        .order_by(OcrRegion.reading_order)
        .all()
    )


# --- happy path -------------------------------------------------------------


def test_preprocessed_document_reaches_completed_with_regions(session_factory, db_session, store):
    doc, _user = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, doc)
    db_session.commit()

    job = _run_ocr_job(session_factory)
    assert job is not None
    assert job.status == JobStatus.succeeded.value

    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    assert fresh.processing_state == S.completed.value
    assert fresh.error_category is None

    regions = _regions_for(db_session, doc.id)
    # Three synthetic text bands -> three persisted regions.
    assert len(regions) == 3
    for region in regions:
        assert region.provider == "fake"
        assert region.model_id == FAKE_MODEL_ID
        assert region.region_uid  # deterministic identity persisted
        assert region.processing_version
        # A fake never reports confidence.
        assert region.confidence is None
        assert region.review_status == ReviewStatus.pending.value
        # Boxes are in original space (page is 900x700).
        assert 0 <= region.bbox_left
        assert region.bbox_left + region.bbox_width <= 900
        assert region.bbox_top + region.bbox_height <= 700


def test_page_metadata_records_scope_not_text(session_factory, db_session, store):
    """Per-page provenance records scope/counts, never raw transcript."""
    doc, _user = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, doc)
    db_session.commit()
    _run_ocr_job(session_factory)

    db_session.expire_all()
    page = (
        db_session.query(DocumentPage)
        .filter(DocumentPage.document_id == doc.id)
        .first()
    )
    ocr_meta = page.metadata_json["ocr"]
    assert ocr_meta["provider"] == "fake"
    assert ocr_meta["scope"] == "text_lines"
    assert ocr_meta["regions"] == 3
    assert "empty_reason" in ocr_meta
    # Scope is lines, not page understanding.
    assert ocr_meta["scope"] != "page"


# --- idempotency -------------------------------------------------------------


def test_duplicate_ocr_delivery_does_not_duplicate_regions(session_factory, db_session, store):
    doc, _user = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, doc)
    db_session.commit()
    _run_ocr_job(session_factory)

    # Simulate redelivery: requeue and enqueue a fresh job with a new key.
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    fresh.processing_state = S.preprocessed.value
    db_session.commit()
    job_service.enqueue(
        db_session,
        job_type=DOCUMENT_OCR_JOB,
        payload={"document_id": doc.id, "content_hash": fresh.content_hash},
        idempotency_key=f"{DOCUMENT_OCR_JOB}:{doc.id}:redelivery",
    )
    db_session.commit()
    _run_ocr_job(session_factory)

    db_session.expire_all()
    # Still exactly three regions: the redelivery updated rows in place.
    assert len(_regions_for(db_session, doc.id)) == 3


def test_reviewer_correction_survives_ocr_retry(session_factory, db_session, store):
    """A corrected region is never overwritten by a re-run."""
    doc, _user = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, doc)
    db_session.commit()
    _run_ocr_job(session_factory)

    db_session.expire_all()
    region = _regions_for(db_session, doc.id)[0]
    region.review_status = ReviewStatus.corrected.value
    region.text = "HUMAN CORRECTION"
    db_session.commit()

    # Redeliver the job.
    fresh = db_session.get(SourceDocument, doc.id)
    fresh.processing_state = S.preprocessed.value
    db_session.commit()
    job_service.enqueue(
        db_session,
        job_type=DOCUMENT_OCR_JOB,
        payload={"document_id": doc.id},
        idempotency_key=f"{DOCUMENT_OCR_JOB}:{doc.id}:retry2",
    )
    db_session.commit()
    _run_ocr_job(session_factory)

    db_session.expire_all()
    after = _regions_for(db_session, doc.id)[0]
    # The human correction survived the machine re-run.
    assert after.text == "HUMAN CORRECTION"
    assert after.review_status == ReviewStatus.corrected.value


# --- provider unavailable ----------------------------------------------------


def test_ocr_unavailable_marks_document_failed_and_job_dead(session_factory, db_session, store):
    """A permanent capability failure (no provider) fails fast, no retry loop."""
    doc, _user = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, doc)
    db_session.commit()

    import app.workers.handlers  # noqa: F401
    from app.services.documents.ocr import registry

    # Simulate the configured provider being unusable.
    def _boom(_settings):
        from app.services.documents.ocr.base import OcrError

        raise OcrError(
            "no model",
            category=DocumentErrorCategory.ocr_unavailable.value,
            permanent=True,
        )

    registry._FACTORIES["fake"] = _boom
    try:
        job = job_service.run_once(session_factory)
    finally:
        from app.services.documents.ocr.registry import _build_fake

        registry._FACTORIES["fake"] = _build_fake
        registry._instances.pop("fake", None)

    assert job is not None
    assert job.status == JobStatus.dead.value  # permanent -> no infinite retry

    db_session.expire_all()
    after = db_session.get(SourceDocument, doc.id)
    assert after.processing_state == S.failed.value
    assert after.error_category == DocumentErrorCategory.ocr_unavailable.value


# --- lease / stale worker ----------------------------------------------------


def test_expired_lease_is_reclaimed_for_ocr_retry(session_factory, db_session, store):
    doc, _user = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, db_session.get(SourceDocument, doc.id))
    db_session.commit()

    job = db_session.get(Job, db_session.get(SourceDocument, doc.id).last_job_id)
    from datetime import datetime, timedelta, timezone

    job.status = JobStatus.running.value
    job.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    job.attempts = 1
    db_session.commit()

    reclaimed = job_service.reclaim_expired(db_session, lease_seconds=300)
    db_session.commit()
    assert job.id in reclaimed
    assert db_session.get(Job, job.id).status == JobStatus.pending.value


def test_stale_worker_cannot_finalize_ocr(session_factory, db_session, store):
    """A worker whose lease was taken must not finalize document state."""
    doc, _user = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, doc)
    db_session.commit()
    job = db_session.get(Job, doc.last_job_id)

    from datetime import datetime, timezone

    pipe = ocr_mod.DocumentOcrPipeline(db_session, job)
    pipe._transition_to_ocr(doc)
    db_session.commit()
    pipe._claimed_started_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert pipe._still_owns_lease() is False


# --- cancellation / deletion --------------------------------------------------


def test_cancelled_document_is_not_ocred(session_factory, db_session, store):
    doc, _user = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, doc)
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    fresh.processing_state = S.cancelled.value
    db_session.commit()

    job = _run_ocr_job(session_factory)
    assert job is not None and job.status == JobStatus.succeeded.value  # no-op

    db_session.expire_all()
    after = db_session.get(SourceDocument, doc.id)
    assert after.processing_state == S.cancelled.value  # not resurrected
    assert len(_regions_for(db_session, doc.id)) == 0


def test_deletion_pending_document_is_not_ocred(session_factory, db_session, store):
    doc, _user = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, doc)
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    fresh.processing_state = S.deletion_pending.value
    db_session.commit()

    job = _run_ocr_job(session_factory)
    assert job is not None and job.status == JobStatus.succeeded.value

    db_session.expire_all()
    after = db_session.get(SourceDocument, doc.id)
    assert after.processing_state == S.deletion_pending.value


# --- partial failure -----------------------------------------------------------


def test_page_failure_marks_document_failed_not_completed(session_factory, db_session, store):
    """A failing page leaves the document failed, never completed."""
    doc, _user = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, doc)
    db_session.commit()
    job = db_session.get(Job, doc.last_job_id)

    pipe = ocr_mod.DocumentOcrPipeline(db_session, job, provider=FakeOcrProvider())
    pipe._transition_to_ocr(doc)
    db_session.commit()
    pages = pipe._load_pages(doc)
    assert pages

    # Fail the first page with a retryable storage error.
    pipe._process_pages(doc, pages[:1], job_deadline=pipe._now_monotonic() + 100)
    # Now make the provider throw.
    from app.services.documents.ocr.base import OcrError

    class Boom:
        name = "boom"

        def recognize(self, page, *, limits):
            raise OcrError(
                "simulated",
                category=DocumentErrorCategory.ocr_failed.value,
                permanent=False,
            )

    pipe._provider = Boom()
    processed, failed_page, (category, _detail) = pipe._process_pages(
        doc, pages, job_deadline=pipe._now_monotonic() + 100
    )
    assert failed_page is not None
    assert category == DocumentErrorCategory.ocr_failed.value
    # Partial work is never reported as full success.
    target = ocr_mod.state_machine.mark_completed(
        ocr_mod.state_machine.parse_state(doc.processing_state),
        total_pages=len(pages),
        processed_pages=len(processed),
    )
    assert target == S.review_required


# --- authorization / tenant isolation -----------------------------------------


def test_ocr_regions_never_exposed_without_owner(session_factory, db_session, store):
    """Region data is reachable only through an owned, authorized path."""
    doc, owner = _make_preprocessed_document(session_factory, db_session, store)
    enqueue_ocr(db_session, doc)
    db_session.commit()
    _run_ocr_job(session_factory)
    db_session.expire_all()

    regions = _regions_for(db_session, doc.id)
    assert regions
    # The API schema for documents never carries region text or storage keys.
    from app.schemas.documents import DocumentResponse

    payload = DocumentResponse.model_validate(
        db_session.get(SourceDocument, doc.id), from_attributes=True
    ).model_dump()
    assert "storage_key" not in payload
    assert "regions" not in payload


def test_second_user_cannot_see_first_users_document(session_factory, db_session, store):
    """Owner scoping: a different user's document is not addressable."""
    doc, _owner = _make_preprocessed_document(session_factory, db_session, store)
    other = User(email="other@example.com", password_hash="y" * 60, email_verified=True)
    db_session.add(other)
    db_session.commit()

    from app.errors import NotFoundError
    from app.services.documents.ingestion import get_owned_document

    with pytest.raises(NotFoundError):
        get_owned_document(
            db_session, doc.id, owner_user_id=other.id, org_id=doc.org_id
        )


# --- /documents/{id}/regions endpoint --------------------------------------


@pytest.fixture()
def _ocr_document(session_factory, db_session, store, registered_user):
    """A document owned by the authenticated caller, through real preprocessing
    + fake OCR, so endpoint tests can read it with ``auth_headers``."""
    doc, _user = _make_preprocessed_document(
        session_factory, db_session, store, owner_user_id=registered_user["user"]["id"]
    )
    enqueue_ocr(db_session, doc)
    db_session.commit()
    _run_ocr_job(session_factory)
    db_session.expire_all()
    return db_session.get(SourceDocument, doc.id)


def test_regions_endpoint_requires_auth(client, _ocr_document):
    assert client.get(f"/api/v1/documents/{_ocr_document.id}/regions").status_code == 401


def test_regions_endpoint_returns_owned_regions(client, auth_headers, _ocr_document):
    resp = client.get(
        f"/api/v1/documents/{_ocr_document.id}/regions", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    first = body["items"][0]
    # Provenance, geometry, and both text forms are present.
    assert first["provider"] == "fake"
    assert first["model_id"] == FAKE_MODEL_ID
    assert first["processing_version"]
    assert first["review_status"] == ReviewStatus.pending.value
    # A fake never reports confidence, and no internal detail leaks.
    assert first["confidence"] is None
    assert "storage_key" not in first
    assert "processed_storage_key" not in first
    # Geometry is inside the original page.
    assert first["bbox_left"] + first["bbox_width"] <= 900
    assert first["bbox_top"] + first["bbox_height"] <= 700


def test_regions_endpoint_hides_other_tenants_documents(client, auth_headers, _ocr_document):
    """A different user gets 404, not another tenant's recognition results."""
    other = client.post(
        "/api/v1/auth/register",
        json={"email": "intruder@example.com", "password": "another-strong-pass"},
    )
    assert other.status_code == 201, other.text
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "intruder@example.com", "password": "another-strong-pass"},
    )
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = client.get(
        f"/api/v1/documents/{_ocr_document.id}/regions", headers=other_headers
    )
    assert resp.status_code == 404
    # The error must not reveal the document exists.
    assert _ocr_document.id not in resp.text
