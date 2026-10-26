"""Checkpoint F: end-to-end document workflow validation.

These tests drive the **real application stack** in one continuous scenario —
authenticated multipart upload over HTTP, the real private object store, the
real SQLite session, the real durable job queue, and the registered
``document.process`` / ``document.ocr`` / ``document.segment`` handlers — then
read the results back and mutate them through the real API. Nothing is
reimplemented for the test's convenience.

What is exercised, in order
---------------------------
1.  ``POST /api/v1/documents/uploads``  — auth, size/type validation, private
    storage, row persistence, ``document.process`` enqueue.
2.  ``document.process`` handler         — preprocessing, page persistence.
3.  ``document.ocr`` handler             — provider selection, line extraction,
    coordinate mapping, region persistence with provenance.
4.  ``POST /api/v1/documents/{id}/segment`` — scheduling on its own status axis.
5.  ``document.segment`` handler         — question/answer structure.
6.  ``GET  /api/v1/documents/{id}/segments`` — structured retrieval.
7.  ``POST /api/v1/documents/{id}/segments/{sid}/review`` — human correction.
8.  Re-run + redelivery                  — reviewer work preserved, no
    duplicate segments.

Honest boundaries (see the notes at each assertion)
---------------------------------------------------
* **OCR runs over the real HTTP trigger.** ``POST /api/v1/documents/{id}/ocr``
  schedules the work (202, audited, eligibility-checked); the registered
  ``document.ocr`` handler executes it. The upload -> preprocess -> OCR hop is
  therefore verified end to end over HTTP, not at the service boundary.
* **The OCR text is fixture-derived, not recognized.** The real ``fake``
  provider runs the real line extractor and the real coordinate mapping, so
  geometry and persistence are genuinely exercised; only the string content is
  a fixed fixture. Nothing here supports any claim about TrOCR accuracy, which
  remains unverified because the weights cannot be downloaded in this
  environment.
"""

from __future__ import annotations

import io

# Registers the durable job handlers (document.process / .ocr / .segment).
import app.workers.handlers  # noqa: E402,F401
import pytest
from app.config import load_settings
from app.models.documents import (
    DocumentPage,
)
from app.models.documents import DocumentProcessingState as S
from app.models.documents import (
    DocumentSegment,
    OcrRegion,
    ReviewStatus,
    SegmentationStatus,
    SegmentRegion,
    SegmentType,
)
from app.models.job import Job, JobStatus
from app.models.knowledge import SourceDocument
from app.services import jobs as job_service
from app.services.documents.ocr import registry
from app.services.documents.ocr.fake import FAKE_MODEL_ID, FakeOcrProvider
from app.services.storage import get_object_store, reset_object_store_for_tests

# Fixture text cycled per detected text line. Three bands are drawn in the page
# image, so the real line extractor yields exactly three regions.
FIXTURE_TEXTS = ("1. What is 2+2?", "Ans: 4", "2. Name a gas.")


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_providers():
    """Never leak a cached provider instance between tests."""
    registry.reset_ocr_providers_for_tests()
    yield
    registry.reset_ocr_providers_for_tests()


@pytest.fixture()
def storage_dir(tmp_path, monkeypatch):
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
    """A synthetic page with exactly three text-like bands."""
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


def _run_queue(session_factory, *, limit=12) -> list:
    """Run registered handlers until the queue is empty.

    Fails loudly with the recorded job error if any handler did not succeed, so
    a broken stage reports its cause instead of silently producing no output.
    """
    ran = []
    for _ in range(limit):
        job = job_service.run_once(session_factory)
        if job is None:
            break
        ran.append(job)
    for job in ran:
        assert job.status == JobStatus.succeeded.value, (
            f"{job.job_type} did not succeed: {job.status} / {job.error}"
        )
    return ran


def _use_fixture_texts():
    """Make the real ``fake`` provider emit question-like fixture strings.

    Swapping the factory is the sanctioned seam the Checkpoint D tests already
    use for failure injection; the provider still runs the genuine line
    extractor and coordinate mapper.
    """
    original = registry._FACTORIES["fake"]
    registry._FACTORIES["fake"] = lambda _settings: FakeOcrProvider(texts=FIXTURE_TEXTS)
    return original


def _restore_factory(original):
    registry._FACTORIES["fake"] = original
    registry._instances.pop("fake", None)


def _segments_for(session, doc_id):
    return (
        session.query(DocumentSegment)
        .filter(DocumentSegment.document_id == doc_id)
        .order_by(DocumentSegment.reading_order)
        .all()
    )


def _links_for(session, doc_id):
    return (
        session.query(SegmentRegion)
        .join(DocumentSegment, SegmentRegion.segment_id == DocumentSegment.id)
        .filter(DocumentSegment.document_id == doc_id)
        .all()
    )


def _other_client(client):
    """A second, unrelated tenant."""
    email = "outsider@example.com"
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "unrelated-strong-pass"},
    )
    assert reg.status_code == 201, reg.text
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "unrelated-strong-pass"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


# --- the full workflow ------------------------------------------------------


def test_full_workflow_upload_to_reviewed_segment(
    client,
    auth_headers,
    registered_user,
    session_factory,
    db_session,
    store,
):
    """One continuous scenario across every stage that the app exposes."""
    base_factory = _use_fixture_texts()
    try:
        # --- 1. authenticated upload -------------------------------------
        up = client.post(
            "/api/v1/documents/uploads",
            files={"file": ("exam.png", _png(), "image/png")},
            headers=auth_headers,
        )
        assert up.status_code == 201, up.text
        doc_id = up.json()["id"]
        # The API never leaks the private storage key.
        assert "storage_key" not in up.json()
        assert up.json()["processing_state"] in {"uploaded", "queued"}

        doc = db_session.get(SourceDocument, doc_id)
        ocr_axis_after_upload = doc.segmentation_status
        assert ocr_axis_after_upload == SegmentationStatus.not_run.value

        # The original really is stored, privately, under a server-side key.
        assert doc.storage_key
        assert store.exists(doc.storage_key)

        # --- 2. preprocessing --------------------------------------------
        ran = _run_queue(session_factory)
        assert [j.job_type for j in ran] == ["document.process"]
        assert ran[0].status == JobStatus.succeeded.value

        db_session.expire_all()
        doc = db_session.get(SourceDocument, doc_id)
        assert doc.processing_state == S.preprocessed.value
        assert doc.page_count == 1
        assert (
            db_session.query(DocumentPage)
            .filter(DocumentPage.document_id == doc_id)
            .count()
            == 1
        )
        # Segmentation axis untouched by preprocessing.
        assert doc.segmentation_status == SegmentationStatus.not_run.value

        # --- 3. OCR ------------------------------------------------------
        # The real HTTP trigger: owner-scoped, eligibility-checked, audited.
        requested = client.post(
            f"/api/v1/documents/{doc_id}/ocr", headers=auth_headers
        )
        assert requested.status_code == 202, requested.text
        assert requested.json()["processing_state"] == S.ocr_processing.value

        ran = _run_queue(session_factory)
        assert [j.job_type for j in ran] == ["document.ocr"]
        assert ran[0].status == JobStatus.succeeded.value

        db_session.expire_all()
        doc = db_session.get(SourceDocument, doc_id)
        assert doc.processing_state == S.completed.value
        # ...and segmentation is *still* untouched: the axes are independent.
        assert doc.segmentation_status == SegmentationStatus.not_run.value

        regions = (
            db_session.query(OcrRegion)
            .filter(OcrRegion.document_id == doc_id)
            .order_by(OcrRegion.reading_order)
            .all()
        )
        assert len(regions) == 3
        for region in regions:
            # Provenance is persisted and attributes the fake as a test double.
            assert region.provider == "fake"
            assert region.model_id == FAKE_MODEL_ID
            assert region.processing_version
            assert region.region_uid
            assert region.confidence is None  # no invented score
            assert region.review_status == ReviewStatus.pending.value
            # Page-local geometry, inside the original 900x700 page.
            assert 0 <= region.bbox_left
            assert region.bbox_left + region.bbox_width <= 900
            assert region.bbox_top + region.bbox_height <= 700
            assert region.page_id is not None

        # --- 4. request segmentation over the API ------------------------
        seg = client.post(f"/api/v1/documents/{doc_id}/segment", headers=auth_headers)
        assert seg.status_code == 202, seg.text
        assert seg.json()["segmentation_status"] == SegmentationStatus.scheduled.value
        # Requesting segmentation must not disturb the OCR lifecycle.
        db_session.expire_all()
        assert (
            db_session.get(SourceDocument, doc_id).processing_state == S.completed.value
        )

        # --- 5. segmentation handler -------------------------------------
        ran = _run_queue(session_factory)
        assert [j.job_type for j in ran] == ["document.segment"]
        assert ran[0].status == JobStatus.succeeded.value

        db_session.expire_all()
        doc = db_session.get(SourceDocument, doc_id)
        assert doc.segmentation_status == SegmentationStatus.completed.value
        assert doc.processing_state == S.completed.value  # axis still untouched
        assert doc.segmentation_error_category is None

        segments = _segments_for(db_session, doc_id)
        assert [s.segment_type for s in segments] == [
            SegmentType.question.value,
            SegmentType.answer.value,
            SegmentType.question.value,
        ]
        # Question/answer structure with a real parent row resolved.
        assert segments[0].parent_id is None
        assert segments[1].parent_id == segments[0].id
        assert segments[1].uncertainty == "clear"
        first_uids = [s.segment_uid for s in segments]
        assert len(set(first_uids)) == 3
        # Total coverage, no double-claiming.
        links = _links_for(db_session, doc_id)
        assert sorted(link.region_id for link in links) == sorted(
            r.id for r in regions
        )
        assert len(links) == 3

        # --- 6. read the structure back through the API ------------------
        listed = client.get(
            f"/api/v1/documents/{doc_id}/segments", headers=auth_headers
        )
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert [i["segment_type"] for i in items] == [
            "question",
            "answer",
            "question",
        ]
        # Each item cites its evidence, with page-local geometry.
        assert items[0]["region_refs"][0]["page_number"] == 1
        assert items[0]["region_refs"][0]["text"] == FIXTURE_TEXTS[0]
        assert items[1]["effective_text"] == FIXTURE_TEXTS[1]
        assert items[1]["parent_id"] is not None

        # --- 7. human review / correction --------------------------------
        answer_id = items[1]["id"]
        review = client.post(
            f"/api/v1/documents/{doc_id}/segments/{answer_id}/review",
            json={"action": "correct", "corrected_text": "Ans: four"},
            headers=auth_headers,
        )
        assert review.status_code == 200, review.text
        assert review.json()["revision"] == 1
        assert review.json()["effective_text"] == "Ans: four"

        # --- 8. explicit reprocess ---------------------------------------
        rerun = client.post(
            f"/api/v1/documents/{doc_id}/segment", headers=auth_headers
        )
        assert rerun.status_code == 202, rerun.text
        ran = _run_queue(session_factory)
        assert ran and ran[0].status == JobStatus.succeeded.value

        db_session.expire_all()
        doc = db_session.get(SourceDocument, doc_id)
        assert doc.processing_state == S.completed.value
        segments = _segments_for(db_session, doc_id)
        # Identity is stable and no duplicates were created.
        assert [s.segment_uid for s in segments] == first_uids
        assert len(_links_for(db_session, doc_id)) == 3
        kept = {str(s.id): s for s in segments}[answer_id]
        assert kept.corrected_text == "Ans: four"
        assert kept.review_status == ReviewStatus.corrected.value

        # --- 9. redelivery of the finished job ---------------------------
        job = db_session.get(Job, doc.last_segment_job_id)
        job.status = JobStatus.pending.value
        db_session.commit()
        _run_queue(session_factory)

        db_session.expire_all()
        segments = _segments_for(db_session, doc_id)
        assert [s.segment_uid for s in segments] == first_uids
        assert len(segments) == 3
        assert len(_links_for(db_session, doc_id)) == 3
        # OCR evidence was never mutated by any of it.
        for region in db_session.query(OcrRegion).filter(
            OcrRegion.document_id == doc_id
        ):
            assert region.review_status == ReviewStatus.pending.value
    finally:
        _restore_factory(base_factory)


def test_end_to_end_tenant_isolation(
    client, auth_headers, registered_user, session_factory, db_session, store
):
    """A second tenant sees nothing of the first tenant's document, at any stage."""
    base_factory = _use_fixture_texts()
    try:
        up = client.post(
            "/api/v1/documents/uploads",
            files={"file": ("exam.png", _png(), "image/png")},
            headers=auth_headers,
        )
        assert up.status_code == 201, up.text
        doc_id = up.json()["id"]
        _run_queue(session_factory)
        db_session.expire_all()
        requested = client.post(f"/api/v1/documents/{doc_id}/ocr", headers=auth_headers)
        assert requested.status_code == 202, requested.text
        _run_queue(session_factory)
        db_session.expire_all()
        assert (
            db_session.get(SourceDocument, doc_id).processing_state
            == S.completed.value
        ), "OCR did not complete"
        requested = client.post(
            f"/api/v1/documents/{doc_id}/segment", headers=auth_headers
        )
        assert requested.status_code == 202, requested.text
        assert _run_queue(session_factory)

        other = _other_client(client)
        # Every read and write is refused without disclosing existence.
        for method, path in (
            ("GET", f"/api/v1/documents/{doc_id}"),
            ("GET", f"/api/v1/documents/{doc_id}/processing"),
            ("GET", f"/api/v1/documents/{doc_id}/regions"),
            ("GET", f"/api/v1/documents/{doc_id}/segmentation"),
            ("GET", f"/api/v1/documents/{doc_id}/segments"),
        ):
            r = client.request(method, path, headers=other)
            assert r.status_code == 404, (path, r.status_code, r.text)
            assert doc_id not in r.text

        denied = client.post(f"/api/v1/documents/{doc_id}/segment", headers=other)
        assert denied.status_code == 404
        segment_id = client.get(
            f"/api/v1/documents/{doc_id}/segments", headers=auth_headers
        ).json()["items"][0]["id"]
        denied_review = client.post(
            f"/api/v1/documents/{doc_id}/segments/{segment_id}/review",
            json={"action": "confirm"},
            headers=other,
        )
        assert denied_review.status_code == 404
        assert segment_id not in denied_review.text

        # And the list endpoint never returns it either.
        listing = client.get("/api/v1/documents", headers=other)
        assert listing.status_code == 200
        assert doc_id not in listing.text
    finally:
        _restore_factory(base_factory)


def test_end_to_end_org_scoping_is_enforced(client, auth_headers, store):
    """Document ingestion is org-scoped server-side: membership is required."""
    org = client.post("/api/v1/orgs", json={"name": "North High"}, headers=auth_headers)
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]

    ok = client.post(
        "/api/v1/documents/uploads",
        files={"file": ("exam.png", _png(), "image/png")},
        data={"org_id": org_id},
        headers=auth_headers,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["org_id"] == org_id

    # A non-member cannot ingest into someone else's organization.
    other = _other_client(client)
    denied = client.post(
        "/api/v1/documents/uploads",
        files={"file": ("exam.png", _png(), "image/png")},
        data={"org_id": org_id},
        headers=other,
    )
    assert denied.status_code == 422, denied.text
    assert org_id not in denied.text
