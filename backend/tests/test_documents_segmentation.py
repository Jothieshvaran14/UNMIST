"""Checkpoint E tests: question-and-answer segmentation (22 scenarios).

Scenario index
--------------

S01  single numbered question + explicit answer marker
S02  multi-line question merges within a page
S03  subparts are children of their question
S04  a question continuing across pages is ``suspected_continuation``
S05  sentence-final text at a page top is NOT merged (conservative continuation)
S06  headers / footers / page numbers become furniture, not body
S07  a positional answer is ``ambiguous_boundary`` — never "verified"
S08  duplicate question numbering is ``conflicting_numbering``
S09  unstructured prose falls back to an explicit ``unclassified`` segment
S10  missing OCR text is ``missing_ocr``, never invented
S11  diagram / equation / table detection is conservative
S12  ``segment_uid`` is stable across input reordering and text changes
S13  segment geometry is an anchor box on ``start_page`` only
S14  segmentation is deterministic
S15  every input region is assigned to exactly one segment (no silent drops)
S16  the job completes without touching the OCR ``processing_state``
S17  redelivery / reprocess never duplicates segments
S18  a worker that lost its lease does not finalize
S19  reviewer confirmation/correction survives reprocessing
S20  a document with no OCR regions fails permanently (job dead)
S21  a deletion-pending document is never segmented or resurrected
S22  endpoints require auth + ownership, validate input, and audit review

S01–S15 drive the pure segmenter with synthetic region fixtures (no database,
no model, no download). S16–S21 drive the real ``document.segment`` queue job
over SQLite with hand-written OCR region rows. S22 exercises the HTTP surface.

Segmentation infers *structure* over recognized text. It never mutates
``OcrRegion`` evidence and never decides whether an answer is correct — that is
assessment, which is out of scope for this milestone.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

# Registers the durable job handlers, including ``document.segment``. Importing
# it here (rather than lazily) keeps every test in this module independent of
# import order across the suite.
import app.workers.handlers  # noqa: E402,F401
import pytest
from app.config import load_settings
from app.errors import ConflictError
from app.models import AuditEvent
from app.models.documents import (
    DocumentErrorCategory,
    DocumentPage,
)
from app.models.documents import DocumentProcessingState as S
from app.models.documents import (
    DocumentSegment,
    OcrRegion,
    PageStatus,
    RegionType,
    ReviewStatus,
    SegmentationStatus,
    SegmentRegion,
    SegmentReview,
    SegmentType,
    SegmentUncertainty,
)
from app.models.job import Job, JobStatus
from app.models.knowledge import SourceDocument
from app.services import jobs as job_service
from app.services.documents.ingestion import (
    DOCUMENT_SEGMENT_JOB,
    create_upload,
    enqueue_segmentation,
)
from app.services.documents.segmentation import (
    BaselineSegmenter,
    PageInput,
    RegionInput,
    SegmentationRules,
)
from app.services.storage import get_object_store, reset_object_store_for_tests

# --- fixtures --------------------------------------------------------------


@pytest.fixture()
def storage_dir(tmp_path, monkeypatch):
    """Point the private object store at a temp directory.

    Segmentation itself never touches storage, but seeding a document goes
    through ``create_upload``, which does.
    """
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


# --- synthetic inputs (S01-S15) --------------------------------------------

RULES = SegmentationRules(version="seg-test-v1")


def _r(i, page, ro, top, text, *, h=30, left=60, w=600, rt="text_line"):
    return RegionInput(
        region_id=f"r{i}",
        page_number=page,
        reading_order=ro,
        bbox_left=left,
        bbox_top=top,
        bbox_width=w,
        bbox_height=h,
        text=text,
        region_type=rt,
    )


def _p(n, *, h=1200, w=900):
    return PageInput(page_number=n, width_px=w, height_px=h)


def _seg(regions, pages):
    return BaselineSegmenter(RULES).segment(regions, pages)


def _by_region(result, region_id):
    """The single segment claiming ``region_id`` (asserts non-contradiction)."""
    hits = [s for s in result.segments if region_id in s.region_ids]
    assert len(hits) == 1, f"{region_id} claimed by {len(hits)} segments"
    return hits[0]


# --- persisted-input helpers (S16-S22) --------------------------------------


def _png(width=900, height=700) -> bytes:
    """A synthetic page image, so ``create_upload`` validation passes."""
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


def _seed_document(
    session,
    *,
    owner_user_id,
    org_id=None,
    pages=(),
    state=S.completed.value,
):
    """Create an upload and stamp synthetic recognized regions onto it.

    Checkpoint E consumes *persisted* OCR evidence, so the document is placed
    directly in a post-OCR state with hand-written ``OcrRegion`` rows: no
    preprocessing, no model, no download. Each page entry is a list of
    ``(text, bbox_top)`` tuples read top to bottom.
    """
    doc, _validated, _created = create_upload(
        session,
        data=_png(),
        filename="doc.png",
        owner_user_id=owner_user_id,
        org_id=org_id,
        actor_id=owner_user_id,
    )
    doc.processing_state = state
    doc.page_count = len(pages)
    for page_number, lines in enumerate(pages, start=1):
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
        for order, (text, top) in enumerate(lines):
            session.add(
                OcrRegion(
                    document_id=doc.id,
                    page_id=page.id,
                    region_type=RegionType.text_line.value,
                    reading_order=order,
                    bbox_left=60,
                    bbox_top=top,
                    bbox_width=600,
                    bbox_height=30,
                    text=text,
                    provider="fake",
                    model_id="fake-1",
                    processing_version="proc-v1",
                    review_status=ReviewStatus.pending.value,
                )
            )
    session.commit()
    return doc


def _segments(session, doc_id):
    return (
        session.query(DocumentSegment)
        .filter(DocumentSegment.document_id == doc_id)
        .order_by(DocumentSegment.reading_order)
        .all()
    )


def _links(session, doc_id):
    return (
        session.query(SegmentRegion)
        .join(DocumentSegment, SegmentRegion.segment_id == DocumentSegment.id)
        .filter(DocumentSegment.document_id == doc_id)
        .all()
    )


def _regions(session, doc_id):
    return session.query(OcrRegion).filter(OcrRegion.document_id == doc_id).all()


Q_AND_ANSWER = [
    [("1. What is 2+2?", 100), ("Ans: 4", 160)],
]


# =============================================================================
# S01-S15 — deterministic segmenter over synthetic regions
# =============================================================================


def test_s01_single_numbered_question_with_explicit_answer_marker():
    """A numbered question followed by ``Ans:`` yields two linked segments."""
    res = _seg(
        [_r(1, 1, 0, 100, "1. What is 2+2?"), _r(2, 1, 1, 160, "Ans: 4")],
        [_p(1)],
    )
    q = _by_region(res, "r1")
    a = _by_region(res, "r2")
    assert q.segment_type is SegmentType.question
    assert q.uncertainty is SegmentUncertainty.clear
    assert q.label == "1"
    assert a.segment_type is SegmentType.answer
    assert a.uncertainty is SegmentUncertainty.clear
    assert a.parent_uid == q.segment_uid
    assert q.reading_order < a.reading_order
    assert res.warnings == ()


def test_s02_multiline_question_merges_within_page():
    """A tight vertical gap continues the same block instead of splitting it."""
    res = _seg(
        [
            _r(1, 1, 0, 100, "1. Explain the causes"),
            _r(2, 1, 1, 132, "of the revolution"),
        ],
        [_p(1)],
    )
    assert len(res.segments) == 1
    s = res.segments[0]
    assert s.segment_type is SegmentType.question
    assert s.uncertainty is SegmentUncertainty.clear
    assert s.text == "1. Explain the causes\nof the revolution"
    assert s.region_ids == ["r1", "r2"]


def test_s03_subparts_are_children_of_their_question():
    """``(a)`` / ``(b)`` lines become subparts parented to the open question."""
    res = _seg(
        [
            _r(1, 1, 0, 100, "1. Answer these:"),
            _r(2, 1, 1, 200, "(a) First part"),
            _r(3, 1, 2, 250, "(b) Second part"),
        ],
        [_p(1)],
    )
    assert len(res.segments) == 3
    q = _by_region(res, "r1")
    sa = _by_region(res, "r2")
    sb = _by_region(res, "r3")
    assert q.segment_type is SegmentType.question
    assert sa.segment_type is SegmentType.subpart and sa.label == "a"
    assert sb.segment_type is SegmentType.subpart and sb.label == "b"
    assert sa.parent_uid == q.segment_uid
    assert sb.parent_uid == q.segment_uid
    assert sa.uncertainty is SegmentUncertainty.clear
    assert sa.uncertainty is not SegmentUncertainty.missing_ocr


def test_s04_cross_page_question_is_suspected_continuation():
    """Unfinished text carried to the next page merges, flagged as uncertain."""
    res = _seg(
        [
            _r(1, 1, 0, 1100, "1. Explain the causes of the"),
            _r(2, 2, 0, 80, "revolution in detail."),
        ],
        [_p(1), _p(2)],
    )
    assert len(res.segments) == 1
    s = res.segments[0]
    assert s.segment_type is SegmentType.question
    assert (s.start_page, s.end_page) == (1, 2)
    assert s.uncertainty is SegmentUncertainty.suspected_continuation
    assert s.provenance["spans_pages"] is True


def test_s05_sentence_final_page_top_is_not_merged():
    """Conservative continuation: finished text at a page top starts fresh."""
    res = _seg(
        [
            _r(1, 1, 0, 1100, "1. What is 2+2?"),
            _r(2, 2, 0, 80, "Paris is the capital."),
        ],
        [_p(1), _p(2)],
    )
    assert len(res.segments) == 2
    q = _by_region(res, "r1")
    tail = _by_region(res, "r2")
    # The decline is recorded, so a reviewer can see *why* nothing merged.
    assert q.end_page == 1
    assert q.provenance["declined_continuation"] is True
    assert tail.segment_type is SegmentType.unclassified
    assert tail.start_page == 2


def test_s06_headers_footers_and_page_numbers_are_furniture():
    """Repeated header lines and page numbers leave the body stream."""
    res = _seg(
        [
            _r(1, 1, 0, 40, "Confidential Exam"),
            _r(2, 1, 1, 100, "1. Q one?"),
            _r(3, 1, 2, 160, "Ans: a"),
            _r(4, 1, 3, 1150, "Page 1"),
            _r(5, 2, 0, 40, "Confidential Exam"),
            _r(6, 2, 1, 100, "2. Q two?"),
            _r(7, 2, 2, 160, "Ans: b"),
            _r(8, 2, 3, 1150, "Page 2"),
        ],
        [_p(1), _p(2)],
    )
    assert _by_region(res, "r1").segment_type is SegmentType.page_header
    assert _by_region(res, "r5").segment_type is SegmentType.page_header
    assert _by_region(res, "r4").segment_type is SegmentType.page_number
    assert _by_region(res, "r8").segment_type is SegmentType.page_number
    # Real body content is not swallowed by the furniture rules.
    assert _by_region(res, "r2").segment_type is SegmentType.question
    assert _by_region(res, "r3").segment_type is SegmentType.answer
    assert _by_region(res, "r6").segment_type is SegmentType.question
    assert _by_region(res, "r7").segment_type is SegmentType.answer
    assert len(res.segments) == 8


def test_s07_positional_answer_is_ambiguous_not_verified():
    """Text merely sitting below a question is inference, not verification."""
    res = _seg(
        [
            _r(1, 1, 0, 100, "1. What is the capital of France?"),
            _r(2, 1, 1, 300, "Paris"),
        ],
        [_p(1)],
    )
    q = _by_region(res, "r1")
    a = _by_region(res, "r2")
    assert a.segment_type is SegmentType.answer
    assert a.uncertainty is SegmentUncertainty.ambiguous_boundary
    assert a.provenance["inferred_from_position"] is True
    assert a.provenance["base_uncertainty"] == SegmentUncertainty.ambiguous_boundary.value
    assert a.parent_uid == q.segment_uid
    # No marker was observed — nothing here claims the answer was checked.
    assert "Ans" not in a.text


def test_s08_duplicate_numbering_is_flagged():
    """A second ``1.`` after a ``1.`` is conflicting evidence, not a new truth."""
    res = _seg(
        [_r(1, 1, 0, 100, "1. First"), _r(2, 1, 1, 300, "1. Second")],
        [_p(1)],
    )
    first = _by_region(res, "r1")
    second = _by_region(res, "r2")
    assert first.uncertainty is SegmentUncertainty.clear
    assert second.segment_type is SegmentType.question
    assert second.uncertainty is SegmentUncertainty.conflicting_numbering


def test_s09_unstructured_prose_falls_back_to_unclassified():
    """Content no rule can place is represented, never dropped."""
    res = _seg(
        [
            _r(1, 1, 0, 100, "This document contains no questions at all."),
            _r(2, 1, 1, 132, "It is only running prose."),
        ],
        [_p(1)],
    )
    assert len(res.segments) == 1
    s = res.segments[0]
    assert s.segment_type is SegmentType.unclassified
    assert s.uncertainty is SegmentUncertainty.unclassified
    assert sorted(res.covered_region_ids) == ["r1", "r2"]


def test_s10_missing_ocr_text_is_flagged_not_invented():
    """Absent recognition is reported as absent; no text is synthesized."""
    res = _seg(
        [_r(1, 1, 0, 100, "1. "), _r(2, 1, 1, 300, "   ")],
        [_p(1)],
    )
    assert "empty_region_text_present" in res.warnings
    marker = _by_region(res, "r1")
    blank = _by_region(res, "r2")
    assert marker.segment_type is SegmentType.question
    assert marker.uncertainty is SegmentUncertainty.missing_ocr
    assert marker.text == "1."  # marker only, no invented content
    assert blank.uncertainty is SegmentUncertainty.missing_ocr
    assert blank.text == ""


def test_s11_diagram_equation_table_detection_is_conservative():
    """Captions and formulas are detected; prose *mentioning* them is not."""
    res = _seg(
        [
            _r(1, 1, 0, 100, "Fig. 1: A right triangle"),
            _r(2, 1, 1, 300, "Figure 2 shows the trend."),
            _r(3, 1, 2, 500, "x^2 + 4 = 0"),
            _r(4, 1, 3, 700, "What is x^2 = 4?"),
            _r(5, 1, 4, 900, "| a | b |"),
            _r(6, 1, 5, 1100, "Table 1 shows the data."),
        ],
        [_p(1)],
    )
    assert _by_region(res, "r1").segment_type is SegmentType.diagram
    assert _by_region(res, "r3").segment_type is SegmentType.equation
    assert _by_region(res, "r5").segment_type is SegmentType.table
    # A sentence that mentions a figure/table stays prose.
    assert _by_region(res, "r2").segment_type is not SegmentType.diagram
    assert _by_region(res, "r6").segment_type is not SegmentType.table
    # A question containing math stays prose, not a formula.
    assert _by_region(res, "r4").segment_type is not SegmentType.equation


def test_s12_segment_uid_is_stable_across_reorder_and_text_change():
    """Identity keys off the anchor region + type, never text or ordering."""
    regions = [
        _r(1, 1, 0, 100, "1. What is 2+2?"),
        _r(2, 1, 1, 160, "Ans: 4"),
        _r(3, 1, 2, 300, "2. Name a gas."),
        _r(4, 1, 3, 360, "Ans: oxygen"),
    ]
    base = _seg(regions, [_p(1)])
    assert len(base.segments) == 4

    # (a) Reordered input produces identical identity and order.
    reordered = _seg(list(reversed(regions)), [_p(1)])
    assert [s.segment_uid for s in reordered.segments] == [
        s.segment_uid for s in base.segments
    ]

    # (b) Different recognized text on the same anchor keeps the same identity.
    edited = [regions[0], _r(2, 1, 1, 160, "Ans: five"), regions[2], regions[3]]
    changed = _seg(edited, [_p(1)])
    assert [s.segment_uid for s in changed.segments] == [
        s.segment_uid for s in base.segments
    ]
    assert _by_region(changed, "r2").text == "Ans: five"
    assert _by_region(base, "r2").text == "Ans: 4"


def test_s13_geometry_is_anchor_box_on_start_page_only():
    """A cross-page segment keeps one page's box; pages are never merged."""
    res = _seg(
        [
            _r(1, 1, 0, 1100, "1. Explain the causes of the"),
            _r(2, 2, 0, 80, "revolution in detail."),
        ],
        [_p(1), _p(2)],
    )
    s = res.segments[0]
    assert (s.start_page, s.end_page) == (1, 2)
    assert s.region_ids == ["r1", "r2"]
    # Union of page-1 regions only: page 2's bbox_top=80 must not leak in.
    assert (s.bbox_left, s.bbox_top) == (60, 1100)
    assert (s.bbox_width, s.bbox_height) == (600, 30)


def test_s14_segmentation_is_deterministic():
    """Same regions + same rules => byte-identical output, every time."""
    regions = [
        _r(1, 1, 0, 40, "Confidential Exam"),
        _r(2, 1, 1, 100, "1. Explain the causes of the"),
        _r(3, 1, 2, 400, "(a) Political factors"),
        _r(4, 1, 3, 700, "x^2 + 4 = 0"),
        _r(5, 1, 4, 1150, "Page 1"),
        _r(6, 2, 0, 40, "Confidential Exam"),
        _r(7, 2, 1, 80, "economy of the region."),
        _r(8, 2, 2, 400, "2. Compare the two."),
        _r(9, 2, 3, 700, "Ans: they differ"),
        _r(10, 2, 4, 1150, "Page 2"),
    ]
    pages = [_p(1), _p(2)]

    def fingerprint(res):
        return [
            (
                s.segment_uid,
                s.segment_type,
                s.uncertainty,
                s.start_page,
                s.end_page,
                s.reading_order,
                s.bbox_left,
                s.bbox_top,
                s.text,
                tuple(s.region_ids),
                s.parent_uid,
                s.label,
            )
            for s in res.segments
        ]

    a = _seg(regions, pages)
    b = _seg(regions, pages)
    assert fingerprint(a) == fingerprint(b)
    assert a.covered_region_ids == b.covered_region_ids
    assert a.warnings == b.warnings


def test_s15_every_region_assigned_exactly_once() -> None:
    """Total coverage and non-contradiction hold on a mixed document."""
    regions = [
        _r(1, 1, 0, 40, "Confidential Exam"),
        _r(2, 1, 1, 100, "1. What is 2+2?"),
        _r(3, 1, 2, 160, "Ans: 4"),
        _r(4, 1, 3, 300, "(a) Show your working"),
        _r(5, 1, 4, 500, "Fig. 1: A number line"),
        _r(6, 1, 5, 700, "| 1 | 2 |"),
        _r(7, 1, 6, 900, "   "),
        _r(8, 1, 7, 1150, "Page 1"),
        _r(9, 2, 0, 40, "Confidential Exam"),
        _r(10, 2, 1, 80, "2. Name a gas."),
        _r(11, 2, 2, 400, "Total prose with no markers whatsoever."),
        _r(12, 2, 3, 1150, "Page 2"),
    ]
    pages = [_p(1), _p(2)]
    res = _seg(regions, pages)
    ids = sorted(r.region_id for r in regions)

    # Nothing dropped, nothing invented.
    assert sorted(res.covered_region_ids) == ids
    # Non-contradiction: no region is claimed by two segments.
    claimed: list[str] = []
    for s in res.segments:
        claimed.extend(s.region_ids)
    assert sorted(claimed) == ids
    assert len(claimed) == len(set(claimed))
    assert len(res.segments) >= 4


# =============================================================================
# S16-S21 — durable ``document.segment`` job
# =============================================================================


def test_s16_job_completes_without_touching_ocr_state(
    session_factory, db_session, store, registered_user
):
    """Success updates only the segmentation axis; OCR state is read-only."""
    doc = _seed_document(
        db_session,
        owner_user_id=registered_user["user"]["id"],
        pages=Q_AND_ANSWER,
    )
    ocr_state_before = doc.processing_state
    enqueue_segmentation(db_session, doc, org_id=doc.org_id)
    db_session.commit()
    assert doc.segmentation_status == SegmentationStatus.scheduled.value

    job = job_service.run_once(session_factory)
    assert job is not None and job.job_type == DOCUMENT_SEGMENT_JOB
    assert job.status == JobStatus.succeeded.value

    db_session.expire_all()
    after = db_session.get(SourceDocument, doc.id)
    assert after.segmentation_status == SegmentationStatus.completed.value
    assert after.processing_state == ocr_state_before  # axis untouched
    assert after.segmentation_version == load_settings().segment_algorithm_version
    assert after.segmentation_error_category is None
    assert after.segmentation_error_detail is None

    segments = _segments(db_session, doc.id)
    assert [s.segment_type for s in segments] == [
        SegmentType.question.value,
        SegmentType.answer.value,
    ]
    assert segments[1].parent_id == segments[0].id
    assert segments[0].review_status == ReviewStatus.pending.value
    assert segments[0].uncertainty == SegmentUncertainty.clear.value

    # Evidence link: every region referenced exactly once, page-local geometry
    # preserved on the region (read-only) rather than copied into the segment.
    links = _links(db_session, doc.id)
    assert sorted(link.region_id for link in links) == sorted(
        r.id for r in _regions(db_session, doc.id)
    )
    assert all(link.position >= 0 for link in links)
    linked = {link.region_id for link in links}
    for region in _regions(db_session, doc.id):
        # OCR evidence is read-only for this stage.
        assert region.review_status == ReviewStatus.pending.value
        assert region.id in linked


def test_s17_redelivery_and_reprocess_never_duplicate_segments(
    session_factory, db_session, store, registered_user
):
    """Idempotent segment identity across redelivery *and* a fresh run."""
    doc = _seed_document(
        db_session,
        owner_user_id=registered_user["user"]["id"],
        pages=[
            [
                ("1. What is 2+2?", 100),
                ("Ans: 4", 160),
                ("2. Name three gases.", 300),
                ("Ans: oxygen, nitrogen, argon", 360),
            ]
        ],
    )
    enqueue_segmentation(db_session, doc, org_id=doc.org_id)
    db_session.commit()
    job_service.run_once(session_factory)

    db_session.expire_all()
    first = _segments(db_session, doc.id)
    first_uids = [s.segment_uid for s in first]
    assert len(first) == 4
    assert len(set(first_uids)) == 4
    assert len(_links(db_session, doc.id)) == 4

    # (a) Redeliver the exact same job message.
    job = db_session.get(Job, doc.last_segment_job_id)
    job.status = JobStatus.pending.value
    job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    job_service.run_once(session_factory)

    db_session.expire_all()
    second = _segments(db_session, doc.id)
    assert [s.segment_uid for s in second] == first_uids
    assert len(_links(db_session, doc.id)) == 4

    # (b) An explicit new run (numbered, separately keyed) is also idempotent.
    fresh = db_session.get(SourceDocument, doc.id)
    enqueue_segmentation(db_session, fresh, org_id=doc.org_id)
    db_session.commit()
    assert fresh.segmentation_run == 2
    job_service.run_once(session_factory)

    db_session.expire_all()
    third = _segments(db_session, doc.id)
    assert [s.segment_uid for s in third] == first_uids
    assert len(_links(db_session, doc.id)) == 4
    assert (
        db_session.get(SourceDocument, doc.id).segmentation_status
        == SegmentationStatus.completed.value
    )


def test_s18_stale_worker_does_not_finalize(
    session_factory, db_session, store, registered_user
):
    """A worker whose lease was re-claimed must not report completion."""
    doc = _seed_document(
        db_session,
        owner_user_id=registered_user["user"]["id"],
        pages=Q_AND_ANSWER,
    )
    enqueue_segmentation(db_session, doc, org_id=doc.org_id)
    db_session.commit()

    from app.services.documents import segmentation_pipeline as seg_mod

    job = db_session.get(Job, doc.last_segment_job_id)
    job.status = JobStatus.running.value
    job.started_at = datetime.now(timezone.utc)
    job.attempts = 1
    db_session.commit()

    pipe = seg_mod.DocumentSegmentationPipeline(db_session, job)
    # Another worker re-claimed the job, so our recorded lease start is stale.
    pipe._claimed_started_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert pipe._still_owns_lease() is False

    outcome = pipe.run()
    assert outcome == "lease_lost"

    db_session.expire_all()
    after = db_session.get(SourceDocument, doc.id)
    # In-flight, not complete: a partial run is never reported as a success.
    assert after.segmentation_status == SegmentationStatus.processing.value
    assert after.segmentation_status != SegmentationStatus.completed.value
    assert after.processing_state == S.completed.value


def test_s19_reviewer_corrections_survive_reprocessing(
    session_factory, db_session, store, registered_user
):
    """A reviewed segment is never overwritten, pruned, or reassigned."""
    doc = _seed_document(
        db_session,
        owner_user_id=registered_user["user"]["id"],
        pages=Q_AND_ANSWER,
    )
    enqueue_segmentation(db_session, doc, org_id=doc.org_id)
    db_session.commit()
    job_service.run_once(session_factory)

    db_session.expire_all()
    segments = _segments(db_session, doc.id)
    assert len(segments) == 2
    question = segments[0]
    answer = segments[1]
    assert question.segment_type == SegmentType.question.value
    assert answer.segment_type == SegmentType.answer.value
    question_id = question.id
    answer_id = answer.id
    original_text = question.text
    question_region_ids = sorted(
        link.region_id for link in _links(db_session, doc.id) if link.segment_id == question_id
    )
    assert len(question_region_ids) == 1

    # Record what the review endpoint writes.
    question.review_status = ReviewStatus.confirmed.value
    question.corrected_text = "1. What does two plus two equal?"
    question.reviewed_by = registered_user["user"]["id"]
    question.reviewed_at = datetime.now(timezone.utc)
    db_session.add(
        SegmentReview(
            segment_id=question_id,
            revision=1,
            previous_text=original_text,
            corrected_text=question.corrected_text,
            reason="checked against the source",
            reviewer_id=registered_user["user"]["id"],
        )
    )
    db_session.commit()

    # Re-recognize the second region differently so the structure *changes*
    # (the next run wants region 2 inside the question segment).
    answer_region_id = sorted(
        link.region_id for link in _links(db_session, doc.id) if link.segment_id == answer_id
    )[0]
    answer_region = db_session.get(OcrRegion, answer_region_id)
    answer_region.text = "paris"
    db_session.commit()

    # Reprocess under a new algorithm version.
    fresh = db_session.get(SourceDocument, doc.id)
    enqueue_segmentation(db_session, fresh, org_id=doc.org_id)
    db_session.commit()
    job_service.run_once(session_factory)

    db_session.expire_all()
    segments = _segments(db_session, doc.id)
    by_id = {str(s.id): s for s in segments}
    kept = by_id.get(str(question_id))
    assert kept is not None, "reviewer-confirmed segment was pruned"
    # Not overwritten by the machine run.
    assert kept.review_status == ReviewStatus.confirmed.value
    assert kept.corrected_text == "1. What does two plus two equal?"
    assert kept.text == original_text
    assert kept.reviewed_by == registered_user["user"]["id"]

    # The reviewer's region was not reassigned to the new structure.
    kept_regions = sorted(
        link.region_id for link in _links(db_session, doc.id) if link.segment_id == question_id
    )
    assert kept_regions == question_region_ids

    # Every region is still claimed exactly once.
    all_links = _links(db_session, doc.id)
    region_ids = [link.region_id for link in all_links]
    assert sorted(region_ids) == sorted(r.id for r in _regions(db_session, doc.id))
    assert len(region_ids) == len(set(region_ids))

    # The append-only review history survived the reprocess.
    history = (
        db_session.query(SegmentReview)
        .filter(SegmentReview.segment_id == question_id)
        .all()
    )
    assert len(history) == 1
    assert history[0].revision == 1
    assert (
        db_session.get(SourceDocument, doc.id).segmentation_status
        == SegmentationStatus.completed.value
    )
    # The superseded machine answer segment is gone (it was still pending).
    assert str(answer_id) not in by_id


def test_s20_document_without_regions_fails_permanently(
    session_factory, db_session, store, registered_user
):
    """No OCR evidence is an input error: dead job, bounded, no silent success."""
    doc = _seed_document(
        db_session,
        owner_user_id=registered_user["user"]["id"],
        pages=[],
    )

    # The service refuses cleanly up front (defense in depth).
    with pytest.raises(ConflictError):
        enqueue_segmentation(db_session, doc, org_id=doc.org_id)
    db_session.rollback()

    # Bypass that guard to prove the pipeline classifies it too.
    job_service.enqueue(
        db_session,
        job_type=DOCUMENT_SEGMENT_JOB,
        payload={"document_id": doc.id},
        idempotency_key=f"{DOCUMENT_SEGMENT_JOB}:{doc.id}:manual",
    )
    db_session.commit()
    job = job_service.run_once(session_factory)
    assert job is not None and job.status == JobStatus.dead.value

    db_session.expire_all()
    after = db_session.get(SourceDocument, doc.id)
    assert after.segmentation_status == SegmentationStatus.failed.value
    assert after.segmentation_error_category == DocumentErrorCategory.validation.value
    # OCR lifecycle is untouched even though segmentation failed.
    assert after.processing_state == S.completed.value
    assert after.error_category is None
    # Bounded, non-sensitive detail: no paths, keys, or stack traces.
    detail = after.segmentation_error_detail or ""
    assert 0 < len(detail) <= 500
    for leak in ("Traceback", "site-packages", "C:\\", "/home/", "secret", "s3://"):
        assert leak not in detail
    assert len(_segments(db_session, doc.id)) == 0


def test_s21_deletion_pending_document_is_not_segmented(
    session_factory, db_session, store, registered_user
):
    """A document queued for deletion is neither segmented nor resurrected."""
    doc = _seed_document(
        db_session,
        owner_user_id=registered_user["user"]["id"],
        pages=Q_AND_ANSWER,
    )
    enqueue_segmentation(db_session, doc, org_id=doc.org_id)
    db_session.commit()

    db_session.expire_all()
    fresh = db_session.get(SourceDocument, doc.id)
    fresh.processing_state = S.deletion_pending.value
    db_session.commit()

    job = job_service.run_once(session_factory)
    assert job is not None and job.status == JobStatus.succeeded.value  # no-op

    db_session.expire_all()
    after = db_session.get(SourceDocument, doc.id)
    assert after.processing_state == S.deletion_pending.value  # not resurrected
    assert after.segmentation_status == SegmentationStatus.cancelled.value
    assert len(_segments(db_session, doc.id)) == 0
    assert len(_links(db_session, doc.id)) == 0


# =============================================================================
# S22 — HTTP surface: auth, ownership, validation, audit
# =============================================================================


@pytest.fixture()
def _segment_document(db_session, store, registered_user):
    """A document owned by the authenticated caller, with recognized regions."""
    return _seed_document(
        db_session,
        owner_user_id=registered_user["user"]["id"],
        pages=Q_AND_ANSWER,
    )


def _other_headers(client):
    """Register + log in a second, unrelated tenant."""
    email = "intruder@example.com"
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "another-strong-pass"},
    )
    assert reg.status_code == 201, reg.text
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "another-strong-pass"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_s22_endpoints_require_auth_ownership_validate_and_audit(
    client, auth_headers, registered_user, session_factory, db_session, _segment_document
):
    doc = _segment_document
    base = f"/api/v1/documents/{doc.id}"

    # --- authentication --------------------------------------------------
    assert client.get(f"{base}/segments").status_code == 401
    assert client.get(f"{base}/segmentation").status_code == 401
    assert client.post(f"{base}/segment").status_code == 401

    # --- owner: request segmentation -------------------------------------
    resp = client.post(f"{base}/segment", headers=auth_headers)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["segmentation_status"] == SegmentationStatus.scheduled.value
    assert body["segmentation_error_detail"] is None
    # Safe errors/status: never a storage key, path, or stack trace.
    for leak in ("Traceback", "storage_key", "site-packages"):
        assert leak not in resp.text

    status = client.get(f"{base}/segmentation", headers=auth_headers)
    assert status.status_code == 200, status.text
    assert status.json()["segment_available"] is False  # already in flight

    # --- run the job so segments exist ------------------------------------
    job = job_service.run_once(session_factory)
    assert job is not None and job.status == JobStatus.succeeded.value
    db_session.expire_all()

    listed = client.get(f"{base}/segments", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert listed.json()["total"] == 2
    assert [i["segment_type"] for i in items] == ["question", "answer"]
    # Each item exposes its evidence with page-local geometry and its own text.
    refs = items[0]["region_refs"]
    assert len(refs) == items[0]["region_count"] == 1
    assert refs[0]["page_number"] == 1
    assert refs[0]["text"] == "1. What is 2+2?"
    assert refs[0]["bbox_top"] == 100
    assert items[0]["effective_text"] == items[0]["text"]
    assert items[0]["uncertainty"] == SegmentUncertainty.clear.value
    # Segmentation is structure, never an assessment claim.
    assert items[0]["review_status"] == ReviewStatus.pending.value

    # --- review validation + mutation -------------------------------------
    segment_id = items[1]["id"]
    bad_action = client.post(
        f"{base}/segments/{segment_id}/review",
        json={"action": "grade"},
        headers=auth_headers,
    )
    assert bad_action.status_code == 422

    missing_text = client.post(
        f"{base}/segments/{segment_id}/review",
        json={"action": "correct", "corrected_text": "   "},
        headers=auth_headers,
    )
    assert missing_text.status_code == 422

    ok = client.post(
        f"{base}/segments/{segment_id}/review",
        json={"action": "confirm", "reason": "matches the scan"},
        headers=auth_headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["revision"] == 1
    assert ok.json()["review_status"] == ReviewStatus.confirmed.value

    # The mutation is audited, with the actor and the reviewed resource.
    db_session.expire_all()
    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "document.segment_review")
        .all()
    )
    assert len(events) == 1
    assert events[0].resource_id == segment_id
    assert events[0].actor_id == registered_user["user"]["id"]
    assert events[0].details["review_action"] == "confirm"

    # --- tenant isolation (404, no existence disclosure) ------------------
    other = _other_headers(client)
    for method, path in (
        ("GET", f"{base}/segments"),
        ("GET", f"{base}/segmentation"),
    ):
        r = client.request(method, path, headers=other)
        assert r.status_code == 404, (path, r.status_code, r.text)
        assert doc.id not in r.text

    denied = client.post(f"{base}/segment", headers=other)
    assert denied.status_code == 404
    assert doc.id not in denied.text

    denied_review = client.post(
        f"{base}/segments/{segment_id}/review",
        json={"action": "confirm"},
        headers=other,
    )
    assert denied_review.status_code == 404
    assert segment_id not in denied_review.text
