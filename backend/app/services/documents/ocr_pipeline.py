"""Durable ``document.ocr`` pipeline: recoverable handwriting recognition.

This is the worker-side orchestrator that turns persisted, preprocessed page
artifacts into traceable OCR regions. It is a *separate stage* from
``document.process`` (preprocessing), joined by the state machine:

    preprocessed -> ocr_processing -> review_required | completed | failed

Why a separate job rather than a second phase of ``document.process``:

  * Preprocessing and inference have very different cost, dependency, and
    failure profiles. OCR needs an optional ML stack and a loaded model;
    preprocessing must keep working without it.
  * A document can legitimately sit in ``preprocessed`` forever (OCR_PROVIDER
    = none) without that being a failure.
  * Retry policy differs: preprocessing failures are usually I/O; OCR failures
    are usually capability (no model) or resource (timeout/memory).

Recoverability contract (mirrors ``DocumentPipeline``)
------------------------------------------------------
* **Short transactions.** State is committed before any inference, and each
  page's regions are committed as they are written, so a crash never holds a
  DB transaction across a model call.
* **Idempotent regions.** Every region gets a deterministic ``region_uid``
  derived from ``(provider, model_id, processing_version, reading_order)``.
  A redelivered job recomputes the same uids and updates rows in place; the
  unique ``(page_id, region_uid)`` constraint is the backstop.
* **Reviewer corrections survive retries.** A region whose ``review_status``
  is no longer ``pending`` is never overwritten by a re-run.
* **Lease ownership.** Before finalizing, the handler re-reads the job and
  confirms it still owns the lease. A stale worker must not finalize state.
* **Cancellation.** Between pages the handler re-reads the document; if it was
  cancelled or marked for deletion, processing stops without resurrecting it.
* **Partial work is never reported as success.** A page that produced no
  regions records why (blank / no line / timeout), and a page that failed
  leaves the document in ``failed``, never ``completed``.

Honesty rules enforced here
---------------------------
* Running OCR on lines is **not** the same as understanding a page. The result
  scope is recorded per page; nothing here claims question detection, layout
  reconstruction, or grading.
* An empty prediction is recorded as empty with a reason — never promoted to a
  transcript, and never silently retried as if it were a failure.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import Settings, get_settings
from ...errors import NotFoundError
from ...models.documents import (
    DocumentErrorCategory,
    DocumentPage,
)
from ...models.documents import DocumentProcessingState as S
from ...models.documents import (
    OcrRegion,
    ReviewStatus,
)
from ...models.job import Job
from ...models.knowledge import SourceDocument
from ...services import jobs as job_service
from ...services.audit import record_audit
from ...services.storage import ObjectStoreError, get_object_store
from . import state as state_machine
from .ocr.base import (
    OCR_PROCESSING_VERSION,
    OcrError,
    OcrLimits,
    OcrPageInput,
    OcrPageResult,
    OcrProvider,
)
from .ocr.registry import get_ocr_provider
from .preprocess import attempt_temp_dir

# States in which the document must not be (re)OCRed by a worker.
# ``preprocessed`` is the normal entry point, so it is NOT skipped; a redelivered
# job may already be in ``ocr_processing`` and continues idempotently.
_SKIP_STATES = frozenset(
    {S.cancelled, S.deletion_pending, S.deleted, S.completed, S.review_required}
)

# Map OCR failure categories onto the persisted, bounded categories.
_CATEGORY_MAP = {
    "validation": DocumentErrorCategory.validation.value,
    "storage": DocumentErrorCategory.storage.value,
    "persistence": DocumentErrorCategory.persistence.value,
    "ocr_unavailable": DocumentErrorCategory.ocr_unavailable.value,
    "ocr_timeout": DocumentErrorCategory.ocr_timeout.value,
    "ocr_failed": DocumentErrorCategory.ocr_failed.value,
    "preprocessing": DocumentErrorCategory.preprocessing.value,
}


def derive_region_uid(
    *,
    provider: str,
    model_id: str,
    processing_version: str,
    reading_order: int,
) -> str:
    """Deterministic identity for one region within a page.

    Derived from provenance and position, never from the recognized text, so a
    re-run that produces different text still maps onto the same row (updating
    it) rather than creating a duplicate. Two different models processing the
    same page produce disjoint uids, so their regions coexist and remain
    separately attributable.
    """
    basis = f"{provider}|{model_id}|{processing_version}|{reading_order}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:64]


class DocumentOcrPipeline:
    """Runs one ``document.ocr`` job against a single document."""

    def __init__(
        self,
        session: Session,
        job: Job,
        *,
        settings: Optional[Settings] = None,
        store=None,
        provider: Optional[OcrProvider] = None,
    ) -> None:
        self.session = session
        self.job = job
        self.settings = settings or get_settings()
        self.store = store or get_object_store(self.settings)
        self.limits = OcrLimits.from_settings(self.settings)
        # Constructed lazily on first use so a skipped/cancelled job never
        # loads a model.
        self._provider = provider
        self._claimed_started_at: Optional[datetime] = job.started_at

    # -- provider ----------------------------------------------------------

    @property
    def provider(self) -> OcrProvider:
        if self._provider is None:
            self._provider = get_ocr_provider(self.settings)
        return self._provider

    # -- lease -------------------------------------------------------------

    def _still_owns_lease(self) -> bool:
        """True if this worker still holds the job lease."""
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
            raise job_service.PermanentJobError("document.ocr job is missing 'document_id'")

        document = self.session.get(SourceDocument, document_id)
        if document is None:
            # Hard-deleted; nothing to recognize. Idempotent no-op.
            return "document_missing"

        current = state_machine.parse_state(document.processing_state)
        if current in _SKIP_STATES:
            return f"skipped_{current.value}"

        pages = self._load_pages(document)
        if not pages:
            # No prepared pages: preprocessing never completed for this
            # document. Permanent — OCR cannot invent the page images.
            self._fail(
                document,
                DocumentErrorCategory.preprocessing.value,
                "No preprocessed pages are available to recognize",
                permanent=True,
            )
            raise job_service.PermanentJobError("document has no preprocessed pages")

        # --- claim state, then COMMIT to release locks before inference ----
        self._transition_to_ocr(document)
        self.session.commit()

        # Resolving the provider may load a model; do it once, outside any
        # transaction, so a capability failure is a clean classified error.
        try:
            _ = self.provider
        except OcrError as exc:
            self._fail(document, exc.category, exc.message, permanent=exc.permanent)
            if exc.permanent:
                raise job_service.PermanentJobError(exc.message) from exc
            raise

        job_deadline = self._now_monotonic() + self.limits.job_timeout_seconds

        with attempt_temp_dir(self.settings.preprocess_temp_dir or None):
            processed, failed_page, failure = self._process_pages(
                document, pages, job_deadline
            )

        # --- finalize only if we still own the lease -----------------------
        if not self._still_owns_lease():
            return "lease_lost"

        if failed_page is not None:
            category, detail = failure
            self._fail(
                document,
                category,
                detail,
                permanent=False,
                processed=len(processed),
            )
            raise RuntimeError(detail)

        if not self._finalize(document, total=len(pages), processed=len(processed)):
            # Cancelled or deletion started while the pages were being read.
            # Nothing was finalized, so leave the job as a no-op rather than
            # reporting success over a document another flow now owns.
            return "abandoned"
        return f"ocr_{len(processed)}_pages"

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _now_monotonic() -> float:
        import time

        return time.monotonic()

    def _load_pages(self, document: SourceDocument) -> list[DocumentPage]:
        """Prepared pages in reading order, with an artifact to recognize."""
        rows = (
            self.session.execute(
                select(DocumentPage)
                .where(DocumentPage.document_id == document.id)
                .order_by(DocumentPage.page_number.asc())
            )
            .scalars()
            .all()
        )
        return [page for page in rows if page.processed_storage_key]

    def _transition_to_ocr(self, document: SourceDocument) -> None:
        """Validate and move the document into ``ocr_processing``."""
        current = state_machine.parse_state(document.processing_state)
        # A redelivered job may already be in ocr_processing; that is fine.
        if current == S.ocr_processing:
            return
        state_machine.assert_transition(current, S.ocr_processing)
        document.processing_state = S.ocr_processing.value
        document.error_category = None
        document.error_detail = None
        document.processing_version = OCR_PROCESSING_VERSION
        self.session.flush()

    def _process_pages(
        self,
        document: SourceDocument,
        pages: list[DocumentPage],
        job_deadline: float,
    ) -> tuple[list[int], Optional[int], tuple[str, str]]:
        """Recognize each page, persisting regions idempotently per page.

        Returns ``(processed_page_numbers, failed_page_number, (category,
        detail))``. A non-``None`` failed page means processing stopped partway;
        regions already persisted are kept for the next attempt.
        """
        processed: list[int] = []
        for page in pages:
            # Cooperative cancellation check between pages.
            self.session.expire(document, ["processing_state"])
            state = state_machine.parse_state(document.processing_state)
            if state in {S.cancelled, S.deletion_pending, S.deleted}:
                return processed, page.page_number, (
                    DocumentErrorCategory.ocr_failed.value,
                    f"Processing stopped: document entered {state.value}",
                )

            if self._now_monotonic() > job_deadline:
                return processed, page.page_number, (
                    DocumentErrorCategory.ocr_timeout.value,
                    "OCR exceeded the job time budget",
                )

            try:
                self._process_one_page(document, page)
            except OcrError as exc:
                return processed, page.page_number, (exc.category, exc.message)
            except ObjectStoreError:
                return processed, page.page_number, (
                    DocumentErrorCategory.storage.value,
                    "Failed to read page artifact from storage",
                )
            except Exception as exc:  # noqa: BLE001 - classified, not swallowed
                return processed, page.page_number, (
                    DocumentErrorCategory.persistence.value,
                    f"Failed to persist OCR results: {type(exc).__name__}",
                )

            processed.append(page.page_number)
            # Commit per page so a crash loses at most one page of work and
            # never holds a transaction across inference for the document.
            self.session.commit()
        return processed, None, ("", "")

    def _process_one_page(self, document: SourceDocument, page: DocumentPage) -> None:
        """Run OCR for a single page and persist its regions."""
        if not page.processed_storage_key:
            raise OcrError(
                "Page has no preprocessed artifact to recognize",
                category=DocumentErrorCategory.preprocessing.value,
                permanent=True,
            )
        if not page.content_hash:
            raise OcrError(
                "Page artifact has no recorded content hash",
                category=DocumentErrorCategory.preprocessing.value,
                permanent=True,
            )
        try:
            data = self.store.get_verified(page.processed_storage_key, page.content_hash)
        except NotFoundError as exc:
            raise OcrError(
                "Page artifact is missing from storage",
                category=DocumentErrorCategory.storage.value,
                permanent=True,
            ) from exc
        except ObjectStoreError as exc:
            raise OcrError(
                "Failed to read page artifact from storage",
                category=DocumentErrorCategory.storage.value,
                permanent=False,
            ) from exc

        page_input = OcrPageInput(
            document_id=document.id,
            page_id=page.id,
            page_number=page.page_number,
            image_png=data,
            width_px=page.width_px or 0,
            height_px=page.height_px or 0,
            working_scale=page.working_scale or 1.0,
            content_hash=page.content_hash,
        )

        result = self.provider.recognize(page_input, limits=self.limits)
        self._persist_regions(document, page, result)
        self._record_page_outcome(page, result)

    def _persist_regions(
        self,
        document: SourceDocument,
        page: DocumentPage,
        result: OcrPageResult,
    ) -> None:
        """Upsert regions idempotently, preserving reviewer corrections.

        For each region a deterministic ``region_uid`` is computed. If a row
        with that uid already exists it is updated in place — unless a human
        has reviewed it, in which case the machine output is left untouched so
        a retry can never destroy a correction. Regions the provider no longer
        returns are pruned only while still ``pending``.
        """
        seen_uids: set[str] = set()
        for region in result.regions:
            uid = derive_region_uid(
                provider=result.provider,
                model_id=result.model_id,
                processing_version=result.processing_version,
                reading_order=region.reading_order or 0,
            )
            seen_uids.add(uid)

            row = self.session.execute(
                select(OcrRegion).where(
                    OcrRegion.page_id == page.id,
                    OcrRegion.region_uid == uid,
                )
            ).scalar_one_or_none()

            if row is None:
                row = OcrRegion(
                    document_id=document.id,
                    page_id=page.id,
                    region_uid=uid,
                    region_type=region.region_type,
                    provider=result.provider,
                    model_id=result.model_id,
                    model_version=result.model_version,
                    processing_version=result.processing_version,
                    review_status=ReviewStatus.pending.value,
                )
                self.session.add(row)

            # Never overwrite a human correction or confirmation.
            if row.review_status != ReviewStatus.pending.value:
                continue

            row.reading_order = region.reading_order
            row.bbox_left = region.bbox_left
            row.bbox_top = region.bbox_top
            row.bbox_width = region.bbox_width
            row.bbox_height = region.bbox_height
            row.raw_text = region.raw_text
            row.text = region.text
            # NULL means the provider reported no calibrated confidence.
            row.confidence = region.confidence

        # Prune regions the provider no longer returns, but only unreviewed
        # ones: a corrected region is human truth and outlives the model.
        stale = (
            self.session.execute(
                select(OcrRegion).where(
                    OcrRegion.page_id == page.id,
                    OcrRegion.review_status == ReviewStatus.pending.value,
                )
            )
            .scalars()
            .all()
        )
        for row in stale:
            if row.region_uid not in seen_uids:
                self.session.delete(row)
        self.session.flush()

    def _record_page_outcome(self, page: DocumentPage, result: OcrPageResult) -> None:
        """Persist per-page OCR provenance on the page row (no text, no paths)."""
        metadata = dict(page.metadata_json or {})
        metadata["ocr"] = {
            "provider": result.provider,
            "model_id": result.model_id,
            "model_version": result.model_version,
            "processing_version": result.processing_version,
            "scope": result.scope,
            "regions": len(result.regions),
            "duration_ms": result.duration_ms,
            "warnings": list(result.warnings),
            "empty_reason": result.empty_reason,
            "partial": result.partial,
        }
        page.metadata_json = metadata
        self.session.flush()

    def _finalize(
        self, document: SourceDocument, *, total: int, processed: int
    ) -> bool:
        """Move ocr_processing -> completed, or review_required if partial.

        ``mark_completed`` is the single choke point that guarantees partial
        work never becomes ``completed``: when fewer pages were processed than
        exist, the document is routed to ``review_required`` for a human.

        Returns ``False`` — finalizing nothing — when the document left the OCR
        lifecycle while this run was working (cancelled, or deletion started).
        The lifecycle state is re-read here rather than trusted from the
        identity map, because a deletion is committed by a different session
        and the last per-page check may predate it. Writing ``completed`` over
        the top would resurrect a document that is on its way out.
        """
        self.session.expire(document, ["processing_state"])
        state = state_machine.parse_state(document.processing_state)
        if state in {S.cancelled, S.deletion_pending, S.deleted}:
            return False

        target = state_machine.mark_completed(
            state,
            total_pages=total,
            processed_pages=processed,
        )
        document.processing_state = target.value
        document.error_category = None
        document.error_detail = None
        record_audit(
            self.session,
            action="document.ocr_completed",
            org_id=document.org_id,
            resource_type="source_document",
            resource_id=document.id,
            details={
                "pages": processed,
                "total_pages": total,
                "processing_version": OCR_PROCESSING_VERSION,
                "provider": self.provider.name,
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
        processed: int = 0,
    ) -> None:
        """Record a bounded, non-sensitive OCR failure on the document."""
        self.session.rollback()
        fresh = self.session.get(SourceDocument, document.id)
        if fresh is None:
            return
        current = state_machine.parse_state(fresh.processing_state)
        if current in {S.cancelled, S.deletion_pending, S.deleted}:
            # Do not overwrite an explicit cancellation/deletion with failed.
            return
        target = _CATEGORY_MAP.get(category, DocumentErrorCategory.ocr_failed.value)
        if state_machine.can_transition(current, S.failed):
            state_machine.assert_transition(current, S.failed)
            fresh.processing_state = S.failed.value
        fresh.error_category = target
        fresh.error_detail = detail[:500]
        record_audit(
            self.session,
            action="document.ocr_failed",
            org_id=fresh.org_id,
            resource_type="source_document",
            resource_id=fresh.id,
            outcome="failure",
            details={"error_category": target, "permanent": permanent},
        )
        # Commit so the failure decision survives the caller's rollback when
        # the error propagates out of the handler.
        self.session.commit()
