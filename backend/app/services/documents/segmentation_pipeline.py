"""Durable ``document.segment`` pipeline: OCR regions -> reviewable structure.

This is the worker-side orchestrator. It is a *separate stage* from
``document.process`` (preprocessing) and ``document.ocr`` (recognition):

    preprocessed -> ocr -> completed | review_required
                                    \
                                     +-> segmentation (independent axis)

Why a separate status column instead of new ``processing_state`` values:
``processing_state`` describes preprocessing/OCR and ``completed`` is terminal
for it. Segmentation may be requested, re-run, fail, and be re-run again after
OCR is ``completed``, so it keeps its own ``segmentation_status`` and never
mutates the OCR state machine.

Recoverability contract (mirrors ``DocumentOcrPipeline``)
------------------------------------------------------
* **Short transactions.** Candidates are derived outside any transaction and
  persisted in small chunks, each followed by a commit, so a crash loses at
  most one chunk and never holds a lock across the work.
* **Idempotency.** Every segment has a deterministic ``segment_uid`` derived
  from its first contributing region + type. A redelivered job recomputes the
  same uid and updates the row instead of inserting a duplicate.
* **Reviewer protection.** A segment whose ``review_status`` left ``pending``
  is never overwritten and never pruned; regions it owns are never reassigned
  to a different structure. Retries and reprocessing cannot destroy human work.
* **Lease + cancellation.** The worker re-checks that it still owns the job
  lease and that the document has not been cancelled/deleted at safe
  boundaries, and never resurrects a document pending deletion.
* **Honest outcomes.** ``completed`` only when every input region is placed;
  otherwise ``partial`` — never reported as complete success.

Evidence vs inference: this stage only re-arranges recognized text into
candidate structure. It never alters ``OcrRegion`` and never judges whether an
answer is correct (assessment is out of scope).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import Settings, get_settings
from ...models.documents import (
    DocumentErrorCategory,
    DocumentPage,
    DocumentSegment,
    OcrRegion,
    ReviewStatus,
    SegmentationStatus,
    SegmentRegion,
    SegmentType,
)
from ...models.job import Job
from ...models.knowledge import SourceDocument
from ...services import jobs as job_service
from ...services.audit import record_audit
from .segmentation import (
    SEGMENT_ALGORITHM,
    BaselineSegmenter,
    PageInput,
    RegionInput,
    SegmentationError,
    SegmentationResult,
    SegmentationRules,
    SegmentCandidate,
    SegmentLimits,
    derive_segment_uid,
)

# Document states in which a segmentation job must not (re)run. ``preprocessed``
# is NOT skipped by itself, but segmentation requires recognized regions, so a
# document still awaiting OCR is rejected below with a permanent error.
_SKIP_STATES = frozenset({"cancelled", "deletion_pending", "deleted"})

# States that make a document eligible: OCR must have produced regions. We do
# not require ``completed`` because ``review_required`` documents legitimately
# carry regions that a human is reviewing.
_INELIGIBLE_STATES = frozenset({"uploaded", "queued", "preprocessing", "failed"})

# Persist at most this many segments per transaction.
_CHUNK_SIZE = 200

# Failure categories that are permanent input errors (retry can never help).
_PERMANENT_CATEGORIES = frozenset(
    {
        DocumentErrorCategory.validation.value,
        DocumentErrorCategory.ocr_unavailable.value,
    }
)


class DocumentSegmentationPipeline:
    """Runs one ``document.segment`` job against a single document."""

    def __init__(
        self,
        session: Session,
        job: Job,
        *,
        settings: Optional[Settings] = None,
        segmenter: Optional[BaselineSegmenter] = None,
    ) -> None:
        self.session = session
        self.job = job
        self.settings = settings or get_settings()
        self.limits = SegmentLimits.from_settings(self.settings)
        # Built lazily so a skipped/cancelled job never does pointless work.
        self._segmenter = segmenter
        self._claimed_started_at: Optional[datetime] = job.started_at

    # -- segmenter ---------------------------------------------------------

    @property
    def segmenter(self) -> BaselineSegmenter:
        if self._segmenter is None:
            self._segmenter = BaselineSegmenter(
                SegmentationRules.from_settings(self.settings)
            )
        return self._segmenter

    # -- lease -------------------------------------------------------------

    def _still_owns_lease(self) -> bool:
        fresh = self.session.get(Job, self.job.id)
        if fresh is None or fresh.status != "running":
            return False
        if self._claimed_started_at is None or fresh.started_at is None:
            return True
        return fresh.started_at.replace(microsecond=0) == (
            self._claimed_started_at.replace(microsecond=0)
        )

    # -- entry point -------------------------------------------------------

    def run(self) -> Optional[str]:
        """Execute the job. Returns a short outcome string for logging."""
        document_id = self.job.payload.get("document_id")
        if not document_id:
            raise job_service.PermanentJobError(
                "document.segment job is missing 'document_id'"
            )

        document = self.session.get(SourceDocument, document_id)
        if document is None:
            return "document_missing"

        state = (document.processing_state or "").strip().lower()
        if state in _SKIP_STATES:
            # Never resurrect a cancelled/deleting document.
            if document.segmentation_status in {
                SegmentationStatus.scheduled.value,
                SegmentationStatus.processing.value,
            }:
                document.segmentation_status = SegmentationStatus.cancelled.value
                self.session.commit()
            return f"skipped_{state}"

        # --- input validation (permanent: a retry cannot conjure regions) ---
        regions, pages, problems = self._load_input(document)
        if problems:
            self._fail(
                document,
                DocumentErrorCategory.validation.value,
                problems,
                permanent=True,
            )
            raise job_service.PermanentJobError(problems)

        # --- claim the run, then COMMIT before doing the work ---------------
        self._transition_to_processing(document)
        self.session.commit()

        try:
            result = self.segmenter.segment(regions, pages)
        except SegmentationError as exc:
            self._fail(document, exc.category, exc.message, permanent=exc.permanent)
            if exc.permanent:
                raise job_service.PermanentJobError(exc.message) from exc
            raise
        except Exception as exc:  # noqa: BLE001 - classified, never swallowed
            self._fail(
                document,
                DocumentErrorCategory.internal.value,
                f"Segmentation failed: {type(exc).__name__}",
                permanent=False,
            )
            raise

        if len(result.segments) > self.limits.max_segments_per_document:
            detail = (
                f"Segmentation produced {len(result.segments)} candidates, over the "
                f"limit of {self.limits.max_segments_per_document}"
            )
            self._fail(
                document,
                DocumentErrorCategory.validation.value,
                detail,
                permanent=True,
            )
            raise job_service.PermanentJobError(detail)

        outcome = self._persist(document, regions, result)
        if outcome == "cancelled":
            # The document entered a terminal/being-deleted state while we ran.
            # Record that segmentation stopped instead of leaving it "running",
            # and never touch the OCR lifecycle.
            self._mark_cancelled(document)
            return "cancelled"
        if outcome != "persisted":
            # "lease_lost": another worker owns the run now. Leave the status at
            # ``processing`` so this attempt is never reported as a completion.
            return outcome

        # --- finalize only if we still own the lease -----------------------
        if not self._still_owns_lease():
            return "lease_lost"

        if not self._finalize(document, result):
            # Cancelled or deletion started while segments were being written.
            # Recording success now would report a deleting document as fully
            # segmented; let _mark_cancelled record that the run stopped.
            self._mark_cancelled(document)
            return "cancelled"
        return f"segmented_{len(result.segments)}_segments"

    # -- input -------------------------------------------------------------

    def _load_input(
        self, document: SourceDocument
    ) -> tuple[list[RegionInput], list[PageInput], str]:
        """Load recognized regions + page geometry, enforcing input limits.

        Returns ``(regions, pages, problem_detail)``. A non-empty detail is a
        permanent input problem and no segmentation should run.
        """
        state = (document.processing_state or "").strip().lower()
        if state in _INELIGIBLE_STATES:
            return [], [], f"Document is not eligible for segmentation (state={state})"

        rows = self.session.execute(
            select(OcrRegion, DocumentPage)
            .join(DocumentPage, OcrRegion.page_id == DocumentPage.id)
            .where(OcrRegion.document_id == document.id)
            .order_by(DocumentPage.page_number.asc(), OcrRegion.reading_order.asc())
        ).all()
        if not rows:
            return [], [], "Document has no OCR regions to segment"

        if len(rows) > self.limits.max_regions_per_document:
            return [], [], (
                f"Document has {len(rows)} OCR regions, over the segmentation limit "
                f"of {self.limits.max_regions_per_document}"
            )

        page_numbers = {int(page.page_number) for _, page in rows}
        if len(page_numbers) > self.limits.max_pages_per_job:
            return [], [], (
                f"Document spans {len(page_numbers)} pages, over the per-job limit "
                f"of {self.limits.max_pages_per_job}"
            )

        cap = self.limits.max_region_text_length
        regions: list[RegionInput] = []
        for region, page in rows:
            text = region.text or ""
            if len(text) > cap:
                text = text[:cap]
            regions.append(
                RegionInput(
                    region_id=str(region.id),
                    page_number=int(page.page_number),
                    reading_order=int(region.reading_order or 0),
                    bbox_left=int(region.bbox_left),
                    bbox_top=int(region.bbox_top),
                    bbox_width=int(region.bbox_width),
                    bbox_height=int(region.bbox_height),
                    text=text,
                    region_type=region.region_type,
                )
            )

        pages: list[PageInput] = []
        seen_pages: set[int] = set()
        for _, page in rows:
            num = int(page.page_number)
            if num in seen_pages:
                continue
            seen_pages.add(num)
            pages.append(
                PageInput(
                    page_number=num,
                    width_px=page.width_px,
                    height_px=page.height_px,
                )
            )
        return regions, pages, ""

    # -- state -------------------------------------------------------------

    def _transition_to_processing(self, document: SourceDocument) -> None:
        document.segmentation_status = SegmentationStatus.processing.value
        document.segmentation_error_category = None
        document.segmentation_error_detail = None
        self.session.flush()

    # -- persistence -------------------------------------------------------

    def _persist(
        self,
        document: SourceDocument,
        regions: list[RegionInput],
        result: SegmentationResult,
    ) -> str:
        """Upsert segments in short chunks, preserving reviewer work.

        Returns ``"persisted"`` on success, ``"cancelled"`` when the document
        entered a skip state mid-run, or ``"lease_lost"`` when another worker
        took over. In the two non-success cases the job deliberately does *not*
        finalize: partial work is never reported as a completion.
        """
        by_region = {r.region_id: r for r in regions}
        # Regions already owned by a reviewer-approved segment. They are never
        # reassigned to a different structure by a later run.
        preserved = self._preserved_region_ids(document)

        candidates = list(result.segments)
        # Decide up front which segments this run will actually materialize, so
        # machine rows it no longer produces can be pruned *before* any insert.
        # Doing it afterwards would trip ``UNIQUE(segment_regions.region_id)``
        # whenever a structure change moves a region to a different segment.
        planned: dict[str, SegmentCandidate] = {}
        for cand in candidates:
            uid = cand.segment_uid or derive_segment_uid(
                first_region_id=cand.region_ids[0],
                segment_type=cand.segment_type,
            )
            owned = [r for r in cand.region_ids if r not in preserved]
            if owned:
                planned[uid] = cand
        # Prune only unreviewed machine output; reviewer-approved rows are kept
        # (they are never in ``preserved``-less form — their regions are held).
        self._prune_stale(document, set(planned))

        seen_uids: set[str] = set(planned)
        uid_to_row: dict[str, DocumentSegment] = {}

        for start in range(0, len(candidates), _CHUNK_SIZE):
            if not self._still_owns_lease():
                return "lease_lost"
            if self._document_stopped(document):
                return "cancelled"

            for cand in candidates[start : start + _CHUNK_SIZE]:
                uid = cand.segment_uid or derive_segment_uid(
                    first_region_id=cand.region_ids[0],
                    segment_type=cand.segment_type,
                )
                owned = [r for r in cand.region_ids if r not in preserved]
                dropped = [r for r in cand.region_ids if r in preserved]
                if not owned:
                    # Every region it wanted belongs to an approved segment.
                    continue

                found = self.session.execute(
                    select(DocumentSegment).where(
                        DocumentSegment.document_id == document.id,
                        DocumentSegment.segment_uid == uid,
                    )
                ).scalar_one_or_none()
                created = found is None
                row: DocumentSegment
                if found is None:
                    row = DocumentSegment(
                        document_id=document.id,
                        segment_uid=uid,
                        algorithm=SEGMENT_ALGORITHM,
                        algorithm_version=self.segmenter.rules.version,
                        review_status=ReviewStatus.pending.value,
                        segment_type=cand.segment_type.value,
                        label=cand.label,
                        start_page=cand.start_page,
                        end_page=cand.end_page,
                        reading_order=cand.reading_order,
                        # Populated before the flush: these columns are NOT NULL
                        # and the row must be insertable the moment it exists.
                        bbox_left=cand.bbox_left,
                        bbox_top=cand.bbox_top,
                        bbox_width=cand.bbox_width,
                        bbox_height=cand.bbox_height,
                        text=self._assemble_text(by_region, owned),
                        uncertainty=cand.uncertainty.value,
                        provenance=dict(cand.provenance),
                    )
                    self.session.add(row)
                    self.session.flush()
                else:
                    row = found
                uid_to_row[uid] = row

                # Never overwrite a human correction or confirmation.
                if not created and row.review_status != ReviewStatus.pending.value:
                    continue

                row.segment_type = cand.segment_type.value
                row.label = cand.label
                row.start_page = cand.start_page
                row.end_page = cand.end_page
                row.reading_order = cand.reading_order
                self._apply_geometry(row, cand, by_region, owned, dropped)
                row.text = self._assemble_text(by_region, owned)
                row.uncertainty = cand.uncertainty.value
                row.algorithm = SEGMENT_ALGORITHM
                row.algorithm_version = self.segmenter.rules.version
                prov = dict(cand.provenance)
                if dropped:
                    prov["regions_held_by_reviewer"] = len(dropped)
                row.provenance = prov
                self._replace_links(row, owned)

            self.session.commit()

        # Parent links, after every row exists (order-independent).
        self._apply_parents(candidates, uid_to_row)
        self.session.commit()

        # Coverage guard: verify against what is *actually* linked, not what we
        # intended to link. Any input region still unattached gets an explicit
        # ``unclassified`` segment so nothing is silently dropped.
        linked = {
            str(rid)
            for rid in self.session.execute(
                select(SegmentRegion.region_id)
                .join(DocumentSegment, SegmentRegion.segment_id == DocumentSegment.id)
                .where(DocumentSegment.document_id == document.id)
            ).scalars()
        }
        missing = [r for r in regions if r.region_id not in linked]
        if missing:
            self._adopt_uncovered(document, missing, seen_uids)

        self.session.commit()
        return "persisted"

    def _apply_geometry(
        self,
        row: DocumentSegment,
        cand: SegmentCandidate,
        by_region: dict[str, RegionInput],
        owned: list[str],
        dropped: list[str],
    ) -> None:
        """Anchor box on ``start_page`` only — never merged across pages.

        When some regions are held by a reviewer, the box is recomputed from
        the regions this run still owns so geometry never claims a box it does
        not control.
        """
        if not dropped:
            row.bbox_left = cand.bbox_left
            row.bbox_top = cand.bbox_top
            row.bbox_width = cand.bbox_width
            row.bbox_height = cand.bbox_height
            return
        chosen = [
            by_region[r]
            for r in owned
            if r in by_region and by_region[r].page_number == cand.start_page
        ]
        if not chosen:
            chosen = [by_region[r] for r in owned if r in by_region]
        if not chosen:
            return
        left = min(r.bbox_left for r in chosen)
        top = min(r.bbox_top for r in chosen)
        right = max(r.bbox_left + r.bbox_width for r in chosen)
        bottom = max(r.bbox_top + r.bbox_height for r in chosen)
        row.bbox_left = left
        row.bbox_top = top
        row.bbox_width = max(0, right - left)
        row.bbox_height = max(0, bottom - top)

    @staticmethod
    def _assemble_text(
        by_region: dict[str, RegionInput], region_ids: list[str]
    ) -> str:
        parts = []
        for rid in region_ids:
            r = by_region.get(rid)
            if r is None:
                continue
            text = (r.text or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts)

    def _replace_links(self, row: DocumentSegment, region_ids: list[str]) -> None:
        """Make the segment reference exactly ``region_ids``, in order."""
        existing = (
            self.session.execute(
                select(SegmentRegion).where(SegmentRegion.segment_id == row.id)
            )
            .scalars()
            .all()
        )
        if existing:
            for link in existing:
                self.session.delete(link)
            self.session.flush()
        for position, rid in enumerate(region_ids):
            self.session.add(
                SegmentRegion(segment_id=row.id, region_id=rid, position=position)
            )
        self.session.flush()

    def _preserved_region_ids(self, document: SourceDocument) -> set[str]:
        rows = self.session.execute(
            select(SegmentRegion.region_id)
            .join(DocumentSegment, SegmentRegion.segment_id == DocumentSegment.id)
            .where(
                DocumentSegment.document_id == document.id,
                DocumentSegment.review_status != ReviewStatus.pending.value,
            )
        ).scalars()
        return {str(r) for r in rows}

    def _apply_parents(
        self,
        candidates,
        uid_to_row: dict[str, DocumentSegment],
    ) -> None:
        """Resolve ``parent_uid`` -> ``parent_id`` once every row exists.

        Done as a second pass so segment creation never depends on reading
        order: a child may be computed before its parent is persisted.
        """
        for cand in candidates:
            if not cand.parent_uid or not cand.segment_uid:
                continue
            child = uid_to_row.get(cand.segment_uid)
            parent = uid_to_row.get(cand.parent_uid)
            if child is None or parent is None:
                continue
            if child.review_status != ReviewStatus.pending.value:
                continue
            child.parent_id = parent.id
        self.session.flush()

    def _prune_stale(self, document: SourceDocument, keep_uids: set[str]) -> None:
        """Remove machine segments this run no longer produces.

        Only ``pending`` rows are pruned — a reviewer-confirmed or corrected
        segment is never deleted, so a reprocess cannot destroy human work.
        """
        stale = (
            self.session.execute(
                select(DocumentSegment).where(
                    DocumentSegment.document_id == document.id,
                    DocumentSegment.review_status == ReviewStatus.pending.value,
                )
            )
            .scalars()
            .all()
        )
        for row in stale:
            if row.segment_uid not in keep_uids:
                self.session.delete(row)
        self.session.flush()

    def _adopt_uncovered(
        self,
        document: SourceDocument,
        missing: list[RegionInput],
        seen_uids: set[str],
    ) -> None:
        """Give any still-unplaced region an explicit ``unclassified`` segment.

        The segmenter already covers every region, so this is a belt-and-braces
        guard for the reviewer-preservation path: a region whose candidate was
        skipped still gets represented rather than silently dropped.
        """
        next_order = int(
            self.session.execute(
                select(func.coalesce(func.max(DocumentSegment.reading_order), -1))
                .select_from(DocumentSegment)
                .where(DocumentSegment.document_id == document.id)
            ).scalar_one()
        )
        for region in missing:
            uid = derive_segment_uid(
                first_region_id=region.region_id,
                segment_type=SegmentType.unclassified,
            )
            if uid in seen_uids:
                continue
            row = DocumentSegment(
                document_id=document.id,
                segment_uid=uid,
                segment_type=SegmentType.unclassified.value,
                uncertainty="unclassified",
                algorithm=SEGMENT_ALGORITHM,
                algorithm_version=self.segmenter.rules.version,
                review_status=ReviewStatus.pending.value,
                start_page=region.page_number,
                end_page=region.page_number,
                reading_order=next_order,
                bbox_left=region.bbox_left,
                bbox_top=region.bbox_top,
                bbox_width=region.bbox_width,
                bbox_height=region.bbox_height,
                text=(region.text or "").strip() or None,
                provenance={"adopted_uncovered": True},
            )
            self.session.add(row)
            self.session.flush()
            self.session.add(
                SegmentRegion(segment_id=row.id, region_id=region.region_id, position=0)
            )
            seen_uids.add(uid)
            next_order += 1
        self.session.flush()

    # -- cancellation ------------------------------------------------------

    def _document_stopped(self, document: SourceDocument) -> bool:
        self.session.expire(document, ["processing_state"])
        return (document.processing_state or "").strip().lower() in _SKIP_STATES

    def _mark_cancelled(self, document: SourceDocument) -> None:
        """Record that segmentation stopped because the document went away.

        Committed independently so the outcome survives the job succeeding; the
        OCR lifecycle columns are left exactly as they were.
        """
        self.session.rollback()
        fresh = self.session.get(SourceDocument, document.id)
        if fresh is None:
            return
        fresh.segmentation_status = SegmentationStatus.cancelled.value
        self.session.commit()

    # -- finalize / fail ---------------------------------------------------

    def _finalize(self, document: SourceDocument, result: SegmentationResult) -> bool:
        """Record a successful segmentation pass.

        Returns ``False`` — finalizing nothing — when the document left the
        lifecycle while this run was working (cancelled, or deletion started).
        The state is re-read here because a deletion is committed by a
        different session and the last per-chunk check may predate it; marking
        ``completed`` over the top would report a document that is on its way
        out as successfully processed.
        """
        self.session.expire(document, ["processing_state"])
        if (document.processing_state or "") in _SKIP_STATES:
            return False

        document.segmentation_status = SegmentationStatus.completed.value
        document.segmentation_version = self.segmenter.rules.version
        document.segmentation_error_category = None
        document.segmentation_error_detail = None
        record_audit(
            self.session,
            action="document.segmented",
            org_id=document.org_id,
            resource_type="source_document",
            resource_id=document.id,
            details={
                "segments": len(result.segments),
                "algorithm": SEGMENT_ALGORITHM,
                "algorithm_version": self.segmenter.rules.version,
                "warnings": list(result.warnings),
            },
        )
        self.session.flush()
        return True

    def _fail(
        self,
        document: SourceDocument,
        category: str,
        detail: str,
        *,
        permanent: bool,
    ) -> None:
        """Record a bounded, non-sensitive segmentation failure.

        Deliberately does **not** touch ``processing_state``: OCR already
        finished, and moving ``completed`` is not a legal transition. Failure is
        visible on the independent segmentation axis instead.
        """
        self.session.rollback()
        fresh = self.session.get(SourceDocument, document.id)
        if fresh is None:
            return
        state = (fresh.processing_state or "").strip().lower()
        if state in _SKIP_STATES:
            fresh.segmentation_status = SegmentationStatus.cancelled.value
            self.session.commit()
            return
        fresh.segmentation_status = SegmentationStatus.failed.value
        fresh.segmentation_error_category = category
        fresh.segmentation_error_detail = detail[:500]
        record_audit(
            self.session,
            action="document.segmentation_failed",
            org_id=fresh.org_id,
            resource_type="source_document",
            resource_id=fresh.id,
            outcome="failure",
            details={"error_category": category, "permanent": permanent},
        )
        # Commit so the failure decision survives the caller's rollback when
        # the error propagates out of the handler.
        self.session.commit()
