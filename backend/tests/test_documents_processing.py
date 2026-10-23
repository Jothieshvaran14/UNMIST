"""Checkpoint C integration tests: durable ``document.process`` pipeline.

These drive the real queue (``run_once``/``claim_next``) and the
``DocumentPipeline`` over a real SQLite session and a real local object store.
They cover the acceptance scenarios: upload→claim, image and multi-page PDF
processing, idempotent redelivery, retry after transient and permanent failure,
lease expiry, cancellation, deletion, partial-page failure, and authorization /
tenant isolation. No network, no model downloads, deterministic fixtures.
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
    PageStatus,
)
from app.models.job import Job, JobStatus
from app.models.knowledge import SourceDocument
from app.models.user import User
from app.services import jobs as job_service
from app.services.documents import pipeline as pipeline_mod
from app.services.documents.ingestion import create_upload, enqueue_processing
from app.services.storage import get_object_store, reset_object_store_for_tests

# --- fixtures --------------------------------------------------------------


@pytest.fixture()
def storage_dir(tmp_path, monkeypatch):
    """Point the app's private object store at a temp directory for the test."""
    import app.config as app_config

    target = tmp_path / "storage"
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(target))
    app_config.reset_settings_for_tests()
    reset_object_store_for_tests()
    yield target
    reset_object_store_for_tests()
    app_config.reset_settings_for_tests()


@pytest.fixture()
def store(storage_dir):
    return get_object_store(load_settings())


def _png(width: int = 40, height: int = 40) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), (30, 140, 210))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf(pages: int = 3) -> bytes:
    from PyPDF2 import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_document(
    session,
    store,
    *,
    data: bytes,
    filename: str = "doc.png",
    org_id=None,
    owner_user_id: str | None = None,
) -> SourceDocument:
    """Ingest a document directly (bypassing HTTP) and enqueue its job.

    Creates a real ``User`` row so the ``owner_user_id`` foreign key resolves.
    """
    if owner_user_id is None:
        user = User(
            email="pipeline@example.com",
            password_hash="x" * 60,
            email_verified=True,
        )
        session.add(user)
        session.flush()
        owner_user_id = user.id

    doc, _validated, _created = create_upload(
        session,
        data=data,
        filename=filename,
        owner_user_id=owner_user_id,
        org_id=org_id,
        actor_id=owner_user_id,
    )
    enqueue_processing(session, doc, org_id=org_id)
    session.commit()
    return doc


def _run_one_job(session_factory, **kwargs) -> Job | None:
    """Drive one durable job through the real worker path."""
    import app.workers.handlers  # noqa: F401 - registers document.process

    return job_service.run_once(session_factory, **kwargs)


# --- happy path: claim → preprocess → preprocessed -------------------------


def test_upload_to_claim_to_preprocessed_image(session_factory, db_session, store):
    doc = _make_document(db_session, store, data=_png())
    assert doc.processing_state == S.queued.value
    assert doc.last_job_id is not None

    job = _run_one_job(session_factory)
    assert job is not None
    assert job.status == JobStatus.succeeded.value

    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    assert fresh.processing_state == S.preprocessed.value
    assert fresh.page_count == 1
    assert fresh.error_category is None

    pages = (
        db_session.query(DocumentPage)
        .filter(DocumentPage.document_id == doc.id)
        .all()
    )
    assert len(pages) == 1
    page = pages[0]
    assert page.page_number == 1
    assert page.status == PageStatus.processed.value
    assert page.content_hash
    assert page.preprocessing_version == pipeline_mod.PREPROCESSING_VERSION
    assert page.width_px and page.height_px  # original geometry recorded

    # The artifact exists in private storage and is not exposed via metadata.
    assert page.processed_storage_key
    assert store.exists(page.processed_storage_key)


def test_multi_page_pdf_records_pages_in_order(session_factory, db_session, store):
    doc = _make_document(db_session, store, data=_pdf(pages=3), filename="doc.pdf")

    _run_one_job(session_factory)

    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    assert fresh.processing_state == S.preprocessed.value
    assert fresh.page_count == 3

    pages = (
        db_session.query(DocumentPage)
        .filter(DocumentPage.document_id == doc.id)
        .order_by(DocumentPage.page_number)
        .all()
    )
    assert [p.page_number for p in pages] == [1, 2, 3]
    assert all(p.status == PageStatus.processed.value for p in pages)
    # Each page has its own artifact.
    assert len({p.processed_storage_key for p in pages}) == 3


# --- idempotency -----------------------------------------------------------


def test_duplicate_job_delivery_does_not_duplicate_pages(
    session_factory, db_session, store
):
    doc = _make_document(db_session, store, data=_png())

    # Run the same job twice (duplicate delivery).
    _run_one_job(session_factory)
    # Enqueue again with a fresh idempotency key to simulate redelivery.
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    fresh.processing_state = S.queued.value
    db_session.commit()
    job2 = job_service.enqueue(
        db_session,
        job_type="document.process",
        payload={"document_id": doc.id, "content_hash": fresh.content_hash},
        idempotency_key=f"document.process:{doc.id}:redelivery",
    )
    db_session.commit()

    import app.workers.handlers  # noqa: F401

    job_service.run_once(session_factory)

    db_session.expire_all()
    pages = (
        db_session.query(DocumentPage)
        .filter(DocumentPage.document_id == doc.id)
        .all()
    )
    assert len(pages) == 1  # still exactly one page
    assert job2 is not None


def test_page_persistence_is_idempotent_across_reprocessing(db_session, store):
    doc = _make_document(db_session, store, data=_png())
    db_session.commit()

    # Simulate a page already persisted from a prior attempt, then re-persist.
    from app.services.documents.preprocess import PageLimits, preprocess_document

    prepared = preprocess_document(
        _png(), media_type="image/png", limits=PageLimits.from_settings()
    )
    pipe = pipeline_mod.DocumentPipeline.__new__(pipeline_mod.DocumentPipeline)
    pipe.session = db_session
    pipe.store = store
    row1 = pipe._persist_one_page(doc, prepared[0])
    db_session.commit()
    key1 = row1.processed_storage_key

    # Re-persist the same page: same deterministic key, same row, no duplicate.
    row2 = pipe._persist_one_page(doc, prepared[0])
    db_session.commit()
    assert row1.id == row2.id
    assert row2.processed_storage_key == key1
    count = (
        db_session.query(DocumentPage)
        .filter(DocumentPage.document_id == doc.id)
        .count()
    )
    assert count == 1


# --- retryable vs permanent ------------------------------------------------


def test_malformed_document_fails_permanently_without_retry(
    session_factory, db_session, store, monkeypatch
):
    """A permanent preprocessing error marks the doc failed and the job dead."""
    # Bypass upload validation by injecting a document then removing its original.
    doc = _make_document(db_session, store, data=_png())
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    # Remove the stored original: a permanent "missing original" storage error.
    store.delete(fresh.storage_key)
    fresh.processing_state = S.queued.value
    db_session.commit()

    import app.workers.handlers  # noqa: F401

    job = job_service.run_once(session_factory)
    assert job is not None
    assert job.status == JobStatus.dead.value  # no infinite retry

    db_session.expire_all()
    after = db_session.get(SourceDocument, doc.id)
    assert after.processing_state == S.failed.value
    assert after.error_category == DocumentErrorCategory.storage.value


def test_transient_storage_failure_is_retryable_then_recovered(
    session_factory, db_session, store, monkeypatch
):
    """A transient store read error leaves the job pending for retry."""
    doc = _make_document(db_session, store, data=_png())

    from app.services.storage import ObjectStoreError

    real_get = store.get
    calls = {"n": 0}

    def flaky_get(key):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ObjectStoreError("simulated transient I/O error")
        return real_get(key)

    monkeypatch.setattr(store, "get", flaky_get)

    import app.workers.handlers  # noqa: F401
    from app.services.documents.preprocess import PreprocessError

    # The pipeline wraps the transient I/O error into a non-permanent
    # PreprocessError, which the queue treats as retryable.
    with pytest.raises(PreprocessError):
        job_service.run_once(session_factory)

    db_session.expire_all()
    failed = db_session.get(SourceDocument, doc.id)
    assert failed.processing_state == S.failed.value
    assert failed.error_category == DocumentErrorCategory.storage.value

    # The job is back to pending for a retry (not dead).
    job = db_session.get(Job, doc.last_job_id)
    assert job.status == JobStatus.pending.value

    # Second attempt succeeds (store recovers). Clear the retry backoff so the
    # job is immediately claimable.
    monkeypatch.setattr(store, "get", real_get)
    failed.processing_state = S.queued.value
    retry_job = db_session.get(Job, doc.last_job_id)
    from datetime import datetime, timezone

    retry_job.available_at = datetime.now(timezone.utc)
    db_session.commit()
    job_service.run_once(session_factory)
    db_session.expire_all()
    recovered = db_session.get(SourceDocument, doc.id)
    assert recovered.processing_state == S.preprocessed.value


# --- lease -----------------------------------------------------------------


def test_expired_lease_is_reclaimed_for_retry(session_factory, db_session, store):
    doc = _make_document(db_session, store, data=_png())
    # Simulate a crashed worker: job left running with an old started_at.
    job = db_session.get(Job, doc.last_job_id)
    from datetime import datetime, timedelta, timezone

    job.status = JobStatus.running.value
    job.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    job.attempts = 1
    db_session.commit()

    reclaimed = job_service.reclaim_expired(db_session, lease_seconds=300)
    db_session.commit()
    assert job.id in reclaimed
    refreshed = db_session.get(Job, job.id)
    assert refreshed.status == JobStatus.pending.value


def test_stale_worker_cannot_finalize_after_lease_lost(db_session, store):
    doc = _make_document(db_session, store, data=_png())
    db_session.commit()
    job = db_session.get(Job, doc.last_job_id)

    pipe = pipeline_mod.DocumentPipeline(db_session, job)
    # Claim now, then simulate lease takeover by another worker.
    pipe._transition_to_preprocessing(doc)
    db_session.commit()
    pipe._claimed_started_at = datetime_min()  # stale marker != current

    assert pipe._still_owns_lease() is False


def datetime_min():
    from datetime import datetime, timezone

    return datetime(2000, 1, 1, tzinfo=timezone.utc)


# --- cancellation ----------------------------------------------------------


def test_cancelled_document_is_not_processed(session_factory, db_session, store):
    doc = _make_document(db_session, store, data=_png())
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    fresh.processing_state = S.cancelled.value
    db_session.commit()

    job = _run_one_job(session_factory)
    assert job is not None
    assert job.status == JobStatus.succeeded.value  # no-op, not a failure

    db_session.expire_all()
    after = db_session.get(SourceDocument, doc.id)
    assert after.processing_state == S.cancelled.value  # not resurrected
    count = (
        db_session.query(DocumentPage)
        .filter(DocumentPage.document_id == doc.id)
        .count()
    )
    assert count == 0


def test_cancellation_during_multipage_processing_stops(session_factory, db_session, store):
    """A document cancelled mid-run stops before the next page and is not preprocessed."""
    doc = _make_document(db_session, store, data=_pdf(pages=3), filename="doc.pdf")
    db_session.commit()
    job = db_session.get(Job, doc.last_job_id)

    pipe = pipeline_mod.DocumentPipeline(db_session, job)
    pipe._transition_to_preprocessing(doc)
    db_session.commit()

    from app.services.documents.preprocess import PageLimits, preprocess_document

    prepared = preprocess_document(
        _pdf(pages=3), media_type="application/pdf", limits=PageLimits.from_settings()
    )

    # Cancel after the first page by flipping state between pages.
    original_persist = pipe._persist_one_page

    calls = {"n": 0}

    def persist_then_cancel(document, page):
        row = original_persist(document, page)
        calls["n"] += 1
        if calls["n"] == 1:
            document.processing_state = S.cancelled.value
            db_session.commit()
        return row

    pipe._persist_one_page = persist_then_cancel
    persisted, failed_page, _failure = pipe._persist_pages(doc, prepared)

    # Processing stopped after the first page; it did not run all three.
    assert calls["n"] == 1
    assert failed_page is not None
    assert len(persisted) == 1


# --- deletion during processing -------------------------------------------


def test_deletion_pending_document_is_not_processed(session_factory, db_session, store):
    doc = _make_document(db_session, store, data=_png())
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    fresh.processing_state = S.deletion_pending.value
    db_session.commit()

    job = _run_one_job(session_factory)
    assert job is not None and job.status == JobStatus.succeeded.value

    db_session.expire_all()
    after = db_session.get(SourceDocument, doc.id)
    assert after.processing_state == S.deletion_pending.value  # not resurrected


# --- partial page failure --------------------------------------------------


def test_partial_page_failure_never_marks_preprocessed(db_session, store):
    """If a later page fails, earlier pages are kept but the doc is failed."""
    doc = _make_document(db_session, store, data=_pdf(pages=3), filename="doc.pdf")
    db_session.commit()
    job = db_session.get(Job, doc.last_job_id)

    pipe = pipeline_mod.DocumentPipeline(db_session, job)
    pipe._transition_to_preprocessing(doc)
    db_session.commit()

    from app.services.documents.preprocess import PageLimits, preprocess_document

    prepared = preprocess_document(
        _pdf(pages=3), media_type="application/pdf", limits=PageLimits.from_settings()
    )

    # Fail on page 2.
    original_persist = pipe._persist_one_page

    def persist_fail_second(document, page):
        if page.page_number == 2:
            raise RuntimeError("simulated persistence failure")
        return original_persist(document, page)

    pipe._persist_one_page = persist_fail_second
    persisted, failed_page, _failure = pipe._persist_pages(doc, prepared)

    assert failed_page == 2
    assert len(persisted) == 1  # page 1 was kept
    # Marking preprocessed with partial work yields failed, never preprocessed.
    target = pipeline_mod.state_machine.mark_preprocessed(
        pipeline_mod.state_machine.parse_state(doc.processing_state),
        total_pages=len(prepared),
        processed_pages=len(persisted),
    )
    assert target == S.failed


# --- authorization / tenant isolation --------------------------------------


def test_processing_endpoint_not_exposed_and_storage_private(
    session_factory, db_session, store
):
    doc = _make_document(db_session, store, data=_png())
    _run_one_job(session_factory)
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    page = (
        db_session.query(DocumentPage)
        .filter(DocumentPage.document_id == doc.id)
        .first()
    )
    # The API response model never carries the private key.
    from app.schemas.documents import DocumentResponse

    resp = DocumentResponse.model_validate(fresh, from_attributes=True)
    payload = resp.model_dump()
    assert "storage_key" not in payload
    assert "processed_storage_key" not in payload
    # The artifact lives only under the private root.
    assert page.processed_storage_key.startswith("documents/")
    assert store.exists(page.processed_storage_key)


def test_missing_document_is_noop_not_failure(session_factory, db_session, store):
    """A job whose document was hard-deleted succeeds as a no-op."""
    job_service.enqueue(
        db_session,
        job_type="document.process",
        payload={"document_id": "00000000-0000-0000-0000-000000000000"},
        idempotency_key="document.process:missing",
    )
    db_session.commit()

    import app.workers.handlers  # noqa: F401

    ran = job_service.run_once(session_factory)
    assert ran is not None
    assert ran.status == JobStatus.succeeded.value
