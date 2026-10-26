"""Checkpoint G completion tests: OCR trigger, deletion, and purge.

These cover the two entry points that were previously missing from the HTTP
surface (``POST /documents/{id}/ocr`` and ``DELETE /documents/{id}``) and the
``document.purge`` job behind deletion.

They verify, over the real HTTP surface and a real SQLite session:

  * the OCR trigger is owner-scoped, schedules genuine work, and refuses a
    document that has not been preprocessed;
  * deletion is owner-scoped, one-way, idempotent while pending, and moves the
    document to ``deletion_pending``;
  * the purge destroys exactly this document's private storage objects and
    derived rows, tombstones it, and is retry-safe;
  * a purge refuses to touch a document that is not pending (a stale job can
    never destroy a live document) and cannot finalize once its lease is gone;
  * a purged document disappears from reads while unrelated records survive.
"""

from __future__ import annotations

import io
import itertools

import pytest
from app.config import load_settings
from app.models.documents import (
    DocumentPage,
)
from app.models.documents import DocumentProcessingState as S
from app.models.documents import (
    DocumentSegment,
    OcrRegion,
    PageStatus,
    RegionType,
    ReviewStatus,
    SegmentRegion,
    SegmentReview,
)
from app.models.job import Job, JobStatus
from app.models.knowledge import SourceDocument
from app.services import jobs as job_service
from app.services.documents.ingestion import (
    DOCUMENT_PURGE_JOB,
    create_upload,
)
from app.services.documents.purge import DocumentPurgePipeline
from app.services.storage import get_object_store

# --- helpers / fixtures -----------------------------------------------------


# ``create_upload`` dedupes by (owner, content hash), so every seeded document
# needs distinct bytes or the second call returns the first document.
_SEED = itertools.count(1)


def _png_bytes(width: int = 20, height: int = 20, seed: int = 0) -> bytes:
    from PIL import Image

    color = (20 + (seed * 37) % 200, 20 + (seed * 91) % 200, 20 + (seed * 53) % 200)
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def storage_dir(tmp_path, monkeypatch):
    """Point the app's private object store at a temp directory for the test."""
    import app.config as app_config
    import app.services.storage as storage

    target = tmp_path / "storage"
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


def _upload(client, headers, data: bytes, filename: str):
    return client.post(
        "/api/v1/documents/uploads",
        headers=headers,
        files={"file": (filename, data, "application/octet-stream")},
        data={},
    )


def _owner_session_user(db_session):
    """The authenticated caller's user row (alice@example.com).

    When the test uses the ``auth_headers`` fixture, registration already
    created this row and we find it. Purge-only tests skip the fixture, so a
    placeholder row is created instead — either way the seeded documents are
    owned by a real user.
    """
    from app.models.user import User

    user = (
        db_session.query(User).filter(User.email == "alice@example.com").first()
    )
    if user is None:
        user = User(
            email="alice@example.com", password_hash="x" * 60, email_verified=True
        )
        db_session.add(user)
        db_session.flush()
    return user


def _upload_and_preprocess(session_factory, session, client, headers):
    """Upload through the API and run the real worker to ``preprocessed``."""
    import app.workers.handlers  # noqa: F401

    resp = _upload(client, headers, _png_bytes(), "doc.png")
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]

    job_service.run_once(session_factory)
    session.expire_all()
    doc = session.get(SourceDocument, doc_id)
    assert doc.processing_state == S.preprocessed.value, doc.processing_state
    return doc


def _make_document_with_regions(
    session, *, owner_user_id, org_id=None, storage_key=None, pages=1
):
    """Create an upload carrying one page and one recognized region per page.

    Directly seeded (no preprocessing/model) so purge behaviour can be asserted
    against rows the pipeline would really have written.
    """
    doc, _validated, _created = create_upload(
        session,
        data=_png_bytes(seed=next(_SEED)),
        filename="seeded.png",
        owner_user_id=owner_user_id,
        org_id=org_id,
        actor_id=owner_user_id,
    )
    doc.processing_state = S.completed.value
    if storage_key is not None:
        doc.storage_key = storage_key
    for page_number in range(1, pages + 1):
        page = DocumentPage(
            document_id=doc.id,
            page_number=page_number,
            width_px=900,
            height_px=1200,
            status=PageStatus.processed.value,
            metadata_json={},
        )
        session.add(page)
        session.flush()
        session.add(
            OcrRegion(
                document_id=doc.id,
                page_id=page.id,
                region_type=RegionType.text_line.value,
                reading_order=0,
                bbox_left=60,
                bbox_top=80,
                bbox_width=600,
                bbox_height=30,
                text="seeded line",
                provider="fake",
                model_id="fake-1",
                processing_version="proc-v1",
                review_status=ReviewStatus.pending.value,
            )
        )
    session.commit()
    return doc


def _seed_segment(session, doc, region_id, *, uid="seg-1"):
    """Add a segment + region link + reviewer correction under ``doc``."""
    segment = DocumentSegment(
        document_id=doc.id,
        segment_uid=uid,
        segment_type="question",
        start_page=1,
        end_page=1,
        reading_order=0,
        bbox_left=60,
        bbox_top=80,
        bbox_width=600,
        bbox_height=30,
        text="seeded line",
        algorithm="rule_baseline",
        algorithm_version="v1",
        provenance={},
    )
    session.add(segment)
    session.flush()
    session.add(SegmentRegion(segment_id=segment.id, region_id=region_id, position=0))
    session.add(
        SegmentReview(
            segment_id=segment.id,
            revision=1,
            corrected_text="corrected by a human",
            reviewer_id=doc.owner_user_id,
        )
    )
    session.commit()
    return segment


def _count(session, model, doc_id):
    return session.query(model).filter(model.document_id == doc_id).count()


def _run_purge(session, doc, *, job=None):
    """Claim and run the ``document.purge`` job for ``doc``.

    When no explicit job is passed, the queued job is claimed exactly as the
    real worker would (``running`` + ``started_at``) so the pipeline's lease
    check passes. Passing ``job`` directly skips the claim, which is how the
    lost-lease path is exercised.
    """
    import app.workers.handlers  # noqa: F401

    if job is None:
        job = _purge_job(session)
        assert job is not None, "no purge job was enqueued"
        from app.models.base import utcnow

        job.status = JobStatus.running.value
        job.started_at = utcnow()
        session.commit()
    return DocumentPurgePipeline(session, job).run()


def _purge_job(session):
    return (
        session.query(Job)
        .filter(Job.job_type == DOCUMENT_PURGE_JOB)
        .order_by(Job.created_at.desc())
        .first()
    )


def _pend_for_purge(session, doc):
    """Move ``doc`` to ``deletion_pending`` through the real service.

    Uses ``enqueue_deletion`` rather than assigning the state directly, so the
    purge tests always start from a genuinely scheduled job — exactly what the
    ``DELETE`` route produces.
    """
    from app.services.documents.ingestion import enqueue_deletion

    return enqueue_deletion(session, doc, org_id=doc.org_id)


# --- POST /documents/{id}/ocr -----------------------------------------------


def test_ocr_trigger_schedules_recognition(
    session_factory, db_session, client, auth_headers, storage_dir
):
    doc = _upload_and_preprocess(session_factory, db_session, client, auth_headers)

    resp = client.post(f"/api/v1/documents/{doc.id}/ocr", headers=auth_headers)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    # The response is the safe processing view, now in ocr_processing.
    assert body["processing_state"] == S.ocr_processing.value
    assert body["id"] == doc.id
    # Nothing about storage may leak.
    assert "storage_key" not in body

    jobs = db_session.query(Job).filter(Job.job_type == "document.ocr").all()
    assert len(jobs) == 1, jobs
    assert jobs[0].payload["document_id"] == doc.id

    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    assert fresh.processing_state == S.ocr_processing.value


def test_ocr_trigger_refused_when_not_preprocessed(
    db_session, client, auth_headers, storage_dir
):
    """A document with no preprocessed pages gets a clean 409, not a doomed job."""
    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(db_session, owner_user_id=user.id)
    # Simulate "uploaded but preprocessing never ran": no pages, no job.
    db_session.query(DocumentPage).filter(
        DocumentPage.document_id == doc.id
    ).delete(synchronize_session=False)
    doc.processing_state = S.uploaded.value
    doc.last_job_id = None
    db_session.commit()

    out = client.post(f"/api/v1/documents/{doc.id}/ocr", headers=auth_headers)
    assert out.status_code == 409, out.text

    assert db_session.query(Job).filter(Job.job_type == "document.ocr").count() == 0
    db_session.expire_all()
    assert (
        db_session.get(SourceDocument, doc.id).processing_state == S.uploaded.value
    )


def test_ocr_trigger_on_inflight_document_returns_active_job(
    session_factory, db_session, client, auth_headers, storage_dir
):
    """A document still being preprocessed is not re-scheduled.

    ``enqueue_ocr`` hands back the job already doing the work, so no second
    ``document.ocr`` job is created while preprocessing is in flight.
    """
    resp = _upload(client, auth_headers, _png_bytes(), "raw.png")
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    db_session.expire_all()
    before = db_session.get(SourceDocument, doc_id)
    active_job_id = before.last_job_id
    assert active_job_id is not None

    out = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=auth_headers)
    assert out.status_code == 202, out.text
    assert out.json()["last_job_id"] == str(active_job_id)

    assert db_session.query(Job).filter(Job.job_type == "document.ocr").count() == 0


def test_ocr_trigger_is_owner_scoped(
    session_factory, db_session, client, auth_headers, second_user, storage_dir
):
    doc = _upload_and_preprocess(session_factory, db_session, client, auth_headers)
    other = {"Authorization": f"Bearer {second_user['tokens']['access_token']}"}

    out = client.post(f"/api/v1/documents/{doc.id}/ocr", headers=other)
    assert out.status_code == 404, out.text

    db_session.expire_all()
    assert (
        db_session.get(SourceDocument, doc.id).processing_state
        == S.preprocessed.value
    )
    assert db_session.query(Job).filter(Job.job_type == "document.ocr").count() == 0


def test_ocr_trigger_records_an_audit_event(
    session_factory, db_session, client, auth_headers, storage_dir
):
    from app.models.audit import AuditEvent

    doc = _upload_and_preprocess(session_factory, db_session, client, auth_headers)
    client.post(f"/api/v1/documents/{doc.id}/ocr", headers=auth_headers)

    actions = [
        a.action
        for a in db_session.query(AuditEvent)
        .filter(AuditEvent.resource_id == doc.id)
        .all()
    ]
    assert "document.ocr_requested" in actions, actions


# --- DELETE /documents/{id} --------------------------------------------------


def test_delete_schedules_purge_and_pends_document(
    db_session, client, auth_headers, storage_dir
):
    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(
        db_session, owner_user_id=user.id, storage_key="documents/x/original.png"
    )

    resp = client.delete(f"/api/v1/documents/{doc.id}", headers=auth_headers)
    assert resp.status_code == 202, resp.text
    assert resp.json()["processing_state"] == S.deletion_pending.value

    job = _purge_job(db_session)
    assert job is not None, "no purge job was enqueued"
    assert job.payload["document_id"] == doc.id

    db_session.expire_all()
    assert (
        db_session.get(SourceDocument, doc.id).processing_state
        == S.deletion_pending.value
    )


def test_delete_is_idempotent_while_pending(
    db_session, client, auth_headers, storage_dir
):
    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(db_session, owner_user_id=user.id)

    first = client.delete(f"/api/v1/documents/{doc.id}", headers=auth_headers)
    second = client.delete(f"/api/v1/documents/{doc.id}", headers=auth_headers)
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    # Same job returned, never a second purge scheduled.
    assert first.json()["last_job_id"] == second.json()["last_job_id"]

    jobs = db_session.query(Job).filter(Job.job_type == DOCUMENT_PURGE_JOB).count()
    assert jobs == 1, jobs


def test_delete_is_owner_scoped(
    db_session, client, auth_headers, second_user, storage_dir
):
    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(db_session, owner_user_id=user.id)
    other = {"Authorization": f"Bearer {second_user['tokens']['access_token']}"}

    out = client.delete(f"/api/v1/documents/{doc.id}", headers=other)
    assert out.status_code == 404, out.text
    db_session.expire_all()
    assert (
        db_session.get(SourceDocument, doc.id).processing_state
        == S.completed.value
    )


def test_delete_records_an_audit_event(
    db_session, client, auth_headers, storage_dir
):
    from app.models.audit import AuditEvent

    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(db_session, owner_user_id=user.id)
    client.delete(f"/api/v1/documents/{doc.id}", headers=auth_headers)

    actions = [
        a.action
        for a in db_session.query(AuditEvent)
        .filter(AuditEvent.resource_id == doc.id)
        .all()
    ]
    assert "document.delete_requested" in actions, actions


def test_delete_refused_once_already_deleted(
    db_session, client, auth_headers, storage_dir
):
    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(db_session, owner_user_id=user.id)
    _pend_for_purge(db_session, doc)
    db_session.commit()
    assert _run_purge(db_session, doc) == "purged"

    # The tombstone is no longer readable, so a repeat DELETE is a 404.
    out = client.delete(f"/api/v1/documents/{doc.id}", headers=auth_headers)
    assert out.status_code == 404, out.text


def test_delete_completes_through_the_real_queue(
    session_factory, db_session, client, auth_headers, storage_dir
):
    """Full dispatch: HTTP DELETE → queued job → registered handler → tombstone.

    Unlike the direct-pipeline tests, this claims the job with the real queue
    runner, proving ``handle_document_purge`` is registered and the purge
    converges when driven exactly the way the production worker drives it.
    """
    user = _owner_session_user(db_session)
    store = get_object_store(load_settings())
    key = "documents/queue/o.png"
    store.put(key, b"queue-bytes")
    doc = _make_document_with_regions(db_session, owner_user_id=user.id, storage_key=key)

    resp = client.delete(f"/api/v1/documents/{doc.id}", headers=auth_headers)
    assert resp.status_code == 202, resp.text

    job_service.run_once(session_factory)
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    assert fresh.processing_state == S.deleted.value, fresh.processing_state
    assert not store.exists(key)
    assert _count(db_session, DocumentPage, doc.id) == 0

    # The queue reported success, not a silent failure.
    job = _purge_job(db_session)
    assert job.status == JobStatus.succeeded.value, job.status


# --- purge pipeline ---------------------------------------------------------


def test_purge_destroys_storage_rows_and_tombstones(db_session, storage_dir):
    user = _owner_session_user(db_session)
    store = get_object_store(load_settings())

    original_key = "documents/orig/original.png"
    page_key = "documents/orig/pages/p00001.png"
    store.put(original_key, b"original-bytes")
    store.put(page_key, b"page-bytes")

    doc = _make_document_with_regions(
        db_session, owner_user_id=user.id, storage_key=original_key
    )
    page = (
        db_session.query(DocumentPage)
        .filter(DocumentPage.document_id == doc.id)
        .one()
    )
    page.processed_storage_key = page_key
    region = (
        db_session.query(OcrRegion).filter(OcrRegion.document_id == doc.id).one()
    )
    segment = _seed_segment(db_session, doc, region.id)
    db_session.commit()

    _pend_for_purge(db_session, doc)
    db_session.commit()

    outcome = _run_purge(db_session, doc)
    assert outcome == "purged", outcome

    # Private artifacts are gone.
    assert not store.exists(original_key)
    assert not store.exists(page_key)

    # Every derived row for this document is gone, segment children included.
    assert _count(db_session, DocumentPage, doc.id) == 0
    assert _count(db_session, OcrRegion, doc.id) == 0
    assert _count(db_session, DocumentSegment, doc.id) == 0
    assert (
        db_session.query(SegmentRegion)
        .filter(SegmentRegion.segment_id == segment.id)
        .count()
        == 0
    )
    assert (
        db_session.query(SegmentReview)
        .filter(SegmentReview.segment_id == segment.id)
        .count()
        == 0
    )

    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    assert fresh.processing_state == S.deleted.value
    # The pointer to the destroyed object must not outlive it.
    assert fresh.storage_key is None


def test_purge_refuses_document_that_is_not_pending(db_session, storage_dir):
    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(
        db_session, owner_user_id=user.id, storage_key="documents/live/original.png"
    )
    store = get_object_store(load_settings())
    store.put("documents/live/original.png", b"live-bytes")

    # A stale purge job whose document moved back to a healthy state: the
    # job was scheduled while pending, then the row was repurposed.
    _pend_for_purge(db_session, doc)
    doc.processing_state = S.completed.value
    db_session.commit()

    outcome = _run_purge(db_session, doc)
    assert outcome.startswith("skipped_state"), outcome

    # Nothing was destroyed.
    assert store.exists("documents/live/original.png")
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    assert fresh.processing_state == S.completed.value
    assert fresh.storage_key is not None
    assert _count(db_session, DocumentPage, doc.id) == 1
    assert _count(db_session, OcrRegion, doc.id) == 1


def test_purge_rerun_after_completion_is_a_noop(db_session, storage_dir):
    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(
        db_session, owner_user_id=user.id, storage_key="documents/gone/o.png"
    )
    _pend_for_purge(db_session, doc)
    db_session.commit()

    assert _run_purge(db_session, doc) == "purged"
    # Re-delivery of the same job after success must not raise or re-delete.
    db_session.expire_all()
    job = _purge_job(db_session)
    assert job is not None
    assert DocumentPurgePipeline(db_session, job).run() == "already_deleted"
    db_session.expire_all()
    assert (
        db_session.get(SourceDocument, doc.id).processing_state == S.deleted.value
    )


def test_purge_with_lost_lease_does_not_finalize(db_session, storage_dir):
    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(
        db_session, owner_user_id=user.id, storage_key="documents/lease/o.png"
    )
    _pend_for_purge(db_session, doc)
    db_session.commit()

    job = _purge_job(db_session)
    assert job is not None
    # Simulate the job having been taken over: the lease this worker claimed
    # is gone (status is no longer ``running`` under its claim).
    job.status = JobStatus.pending.value
    db_session.commit()

    outcome = _run_purge(db_session, doc, job=job)
    assert outcome == "lease_lost", outcome

    # The stale worker must not have written the tombstone.
    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    assert fresh.processing_state == S.deletion_pending.value
    assert fresh.storage_key is not None


def test_purged_document_is_hidden_from_get_and_list(
    db_session, client, auth_headers, storage_dir
):
    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(db_session, owner_user_id=user.id)
    keep = _make_document_with_regions(db_session, owner_user_id=user.id)

    _pend_for_purge(db_session, doc)
    db_session.commit()
    assert _run_purge(db_session, doc) == "purged"

    # Direct read: 404 once the purge finished.
    assert client.get(f"/api/v1/documents/{doc.id}", headers=auth_headers).status_code == 404

    # List: tombstone excluded, the other document still present.
    listing = client.get("/api/v1/documents", headers=auth_headers)
    assert listing.status_code == 200, listing.text
    ids = [item["id"] for item in listing.json()["items"]]
    assert doc.id not in ids, ids
    assert keep.id in ids, ids


def test_purge_leaves_another_tenants_document_intact(db_session, storage_dir):
    from app.models.user import User

    owner = _owner_session_user(db_session)
    other = User(
        email="bystander@example.com", password_hash="x" * 60, email_verified=True
    )
    db_session.add(other)
    db_session.flush()

    victim = _make_document_with_regions(
        db_session, owner_user_id=owner.id, storage_key="documents/v/o.png"
    )
    bystander = _make_document_with_regions(
        db_session, owner_user_id=other.id, storage_key="documents/b/o.png"
    )
    store = get_object_store(load_settings())
    store.put("documents/v/o.png", b"victim")
    store.put("documents/b/o.png", b"bystander")

    _pend_for_purge(db_session, victim)
    db_session.commit()
    assert _run_purge(db_session, victim) == "purged"

    # The other tenant's document, storage, and rows are untouched.
    db_session.expire_all()
    alive = db_session.get(SourceDocument, bystander.id)
    assert alive.processing_state == S.completed.value
    assert alive.storage_key == "documents/b/o.png"
    assert store.exists("documents/b/o.png")
    assert _count(db_session, DocumentPage, bystander.id) == 1
    assert _count(db_session, OcrRegion, bystander.id) == 1


# --- deletion racing the pipelines ------------------------------------------


def _drain_queue(session_factory, limit: int = 25) -> int:
    """Run the real queue until it reports empty (bounded)."""
    ran = 0
    while ran < limit:
        if job_service.run_once(session_factory) is None:
            break
        ran += 1
    return ran


def test_delete_races_pending_preprocessing_ocr_and_segmentation_jobs(
    session_factory, db_session, client, auth_headers, storage_dir
):
    """Deletion wins over pipeline work that was already queued.

    Three documents are staged so that each stage has a job waiting —
    preprocessing not yet claimed, OCR queued, segmentation queued — and then
    all three are deleted through the real HTTP endpoint. Draining the real
    queue must leave every document deleted with no pages, regions or segments
    written: a pipeline treats ``deletion_pending`` as a skip instead of
    resurrecting the document, the API refuses new work while the purge runs,
    and the queue itself converges (no job left running or failed).
    """
    import app.workers.handlers  # noqa: F401

    user = _owner_session_user(db_session)

    # (a) preprocessing queued, not yet claimed.
    resp_a = _upload(client, auth_headers, _png_bytes(seed=101), "a.png")
    assert resp_a.status_code == 201, resp_a.text
    doc_a_id = resp_a.json()["id"]
    assert (
        client.delete(f"/api/v1/documents/{doc_a_id}", headers=auth_headers).status_code
        == 202
    )

    # (b) preprocessing finished first, then OCR queued but not claimed.
    resp_b = _upload(client, auth_headers, _png_bytes(seed=102), "b.png")
    assert resp_b.status_code == 201, resp_b.text
    doc_b_id = resp_b.json()["id"]

    # FIFO drain: (a)'s process job skips the deleting document, (a)'s purge
    # finalizes it, (b)'s process job prepares it.
    _drain_queue(session_factory)
    db_session.expire_all()
    assert db_session.get(SourceDocument, doc_a_id).processing_state == S.deleted.value
    assert (
        db_session.get(SourceDocument, doc_b_id).processing_state == S.preprocessed.value
    )

    ocr = client.post(f"/api/v1/documents/{doc_b_id}/ocr", headers=auth_headers)
    assert ocr.status_code == 202, ocr.text
    assert ocr.json()["processing_state"] == S.ocr_processing.value

    # Delete while the OCR job is queued and in flight.
    assert (
        client.delete(f"/api/v1/documents/{doc_b_id}", headers=auth_headers).status_code
        == 202
    )

    # While the purge runs, new work is refused — not scheduled, not silently
    # accepted: the document is going away.
    assert (
        client.post(f"/api/v1/documents/{doc_b_id}/ocr", headers=auth_headers).status_code
        == 409
    )
    assert (
        client.post(f"/api/v1/documents/{doc_b_id}/segment", headers=auth_headers).status_code
        == 409
    )

    # (c) a recognized document with segmentation queued, then deleted.
    doc_c = _make_document_with_regions(db_session, owner_user_id=user.id)
    doc_c_id = str(doc_c.id)
    segment = client.post(
        f"/api/v1/documents/{doc_c_id}/segment", headers=auth_headers
    )
    assert segment.status_code == 202, segment.text
    assert (
        client.delete(f"/api/v1/documents/{doc_c_id}", headers=auth_headers).status_code
        == 202
    )

    # Drain everything: OCR and segmentation jobs must skip, both purges run.
    _drain_queue(session_factory)
    db_session.expire_all()

    for doc_id in (doc_a_id, doc_b_id, doc_c_id):
        fresh = db_session.get(SourceDocument, doc_id)
        assert fresh is not None, doc_id
        assert fresh.processing_state == S.deleted.value, fresh.processing_state
        assert fresh.storage_key is None, fresh.storage_key
        # No derived row survives for a deleted document. For (c) this also
        # proves the queued segmentation job never wrote its output.
        assert _count(db_session, DocumentPage, doc_id) == 0
        assert _count(db_session, OcrRegion, doc_id) == 0
        assert _count(db_session, DocumentSegment, doc_id) == 0

    # Nothing was left half-done: every job reached a terminal success.
    unfinished = sorted(
        f"{job.job_type}={job.status}"
        for job in db_session.query(Job).all()
        if job.status not in {JobStatus.succeeded.value, JobStatus.cancelled.value}
    )
    assert unfinished == [], unfinished

    # Reads are gone through the API too (tombstone excluded from responses).
    for doc_id in (doc_a_id, doc_b_id, doc_c_id):
        assert (
            client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers).status_code
            == 404
        )


def test_ocr_and_delete_denied_for_a_different_tenant(
    db_session, client, auth_headers, storage_dir, second_user
):
    """Another account can neither run OCR on nor delete someone else's document.

    The control call proves the refusal is authorization, not eligibility: the
    document is in an OCR-eligible state, its owner gets 202, the other tenant
    gets 404 for both routes and changes nothing.
    """
    user = _owner_session_user(db_session)
    doc = _make_document_with_regions(
        db_session, owner_user_id=user.id, storage_key="documents/x/o.png"
    )
    # Eligible for OCR as far as the state machine is concerned.
    doc.processing_state = S.preprocessed.value
    db_session.commit()

    other = {
        "Authorization": f"Bearer {second_user['tokens']['access_token']}",
    }

    ocr = client.post(f"/api/v1/documents/{doc.id}/ocr", headers=other)
    assert ocr.status_code == 404, ocr.text
    delete = client.delete(f"/api/v1/documents/{doc.id}", headers=other)
    assert delete.status_code == 404, delete.text
    # A cross-tenant read is equally forbidden (no existence leak beyond 404).
    assert client.get(f"/api/v1/documents/{doc.id}", headers=other).status_code == 404

    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    assert fresh.processing_state == S.preprocessed.value, fresh.processing_state
    assert fresh.storage_key == "documents/x/o.png"
    assert db_session.query(Job).filter(Job.job_type == DOCUMENT_PURGE_JOB).count() == 0

    # Control: the owner, on the same document, is allowed to start OCR.
    control = client.post(f"/api/v1/documents/{doc.id}/ocr", headers=auth_headers)
    assert control.status_code == 202, control.text
    assert control.json()["processing_state"] == S.ocr_processing.value
