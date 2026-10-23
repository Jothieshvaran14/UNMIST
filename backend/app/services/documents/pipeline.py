"""Durable ``document.process`` pipeline: recoverable page preprocessing.

This is the worker-side orchestrator that turns an uploaded document into
persisted, normalized page artifacts. It is deliberately *not* OCR: a
successful run means **page preparation is complete**, never that text or
handwriting has been recognized.

Recoverability contract
-----------------------
* **Short transactions.** The DB transaction is committed before any expensive
  rendering and between page batches, so a crash never holds locks during
  inference and never loses all progress.
* **Idempotent pages.** Page rows are keyed by ``(document, page_number)`` and
  artifacts by ``(document, page_number, content_hash)``. A redelivered or
  retried job recomputes the same keys, finds prior work, and skips it.
* **Lease ownership.** Before finalizing, the handler re-reads the job and
  confirms it still owns the lease (``status == running`` and ``started_at``
  unchanged). A worker whose lease expired must not finalize state.
* **Cancellation.** Between pages the handler re-reads the document; if it was
  cancelled or marked for deletion, processing stops without resurrecting it.
* **Partial failure.** Successfully prepared pages are persisted even when a
  later page fails, so a retry reuses them. The document is marked ``failed``
  (never ``preprocessed``/``completed``) when not every page was prepared.

Permanent vs. retryable failures are distinguished so malformed input fails
once and infrastructure errors retry under the queue's bounded backoff.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
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
    PageStatus,
)
from ...models.job import Job, JobStatus
from ...models.knowledge import SourceDocument
from ...services import jobs as job_service
from ...services.audit import record_audit
from ...services.storage import (
    ObjectStoreError,
    generate_page_key,
    get_object_store,
)
from . import state as state_machine
from .preprocess import (
    PREPROCESSING_VERSION,
    PageLimits,
    PreparedPage,
    PreprocessError,
    attempt_temp_dir,
    preprocess_document,
)

# States in which the document must not be (re)processed by a worker.
_SKIP_STATES = frozenset(
    {S.cancelled, S.deletion_pending, S.deleted, S.completed, S.preprocessed}
)

# Map preprocessing failure categories to the persisted, bounded categories.
_CATEGORY_MAP = {
    "validation": DocumentErrorCategory.validation.value,
    "preprocessing": DocumentErrorCategory.preprocessing.value,
    "storage": DocumentErrorCategory.storage.value,
}


class DocumentPipeline:
    """Runs one ``document.process`` job against a single document."""

    def __init__(
        self,
        session: Session,
        job: Job,
        *,
        settings: Optional[Settings] = None,
        store=None,
    ) -> None:
        self.session = session
        self.job = job
        self.settings = settings or get_settings()
        self.store = store or get_object_store(self.settings)
        self.limits = PageLimits.from_settings(self.settings)
        # Lease ownership is established at claim time by the queue.
        self._claimed_started_at: Optional[datetime] = job.started_at
        self._claimed_attempts: Optional[int] = job.attempts

    # -- lease ------------------------------------------------------------

    def _still_owns_lease(self) -> bool:
        """True if this worker still holds the job lease.

        The queue records ``started_at`` on claim. If a lease expired and the
        job was reclaimed (and possibly re-claimed by another worker),
        ``started_at`` changes. A stale worker must not finalize state.
        """
        fresh = self.session.get(Job, self.job.id)
        if fresh is None:
            return False
        if fresh.status != JobStatus.running.value:
            return False
        if self._claimed_started_at is None:
            return True
        if fresh.started_at is None:
            return True
        # Compare as timestamps to tolerate DB microsecond truncation.
        return fresh.started_at.replace(microsecond=0) == (
            self._claimed_started_at.replace(microsecond=0)
        )

    # -- entry point ------------------------------------------------------

    def run(self) -> Optional[str]:
        """Execute the job. Returns a short outcome string for logging."""
        document_id = self.job.payload.get("document_id")
        if not document_id:
            # A job with no document reference can never succeed.
            raise job_service.PermanentJobError(
                "document.process job is missing 'document_id'"
            )

        document = self.session.get(SourceDocument, document_id)
        if document is None:
            # The document was hard-deleted; nothing to process. This is a
            # no-op success, not a retryable failure.
            return "document_missing"

        current = state_machine.parse_state(document.processing_state)
        if current in _SKIP_STATES:
            # Cancelled, deleted, already prepared, or completed: do not
            # resurrect or redo work. Idempotent no-op.
            return f"skipped_{current.value}"

        # --- validate + claim state, then COMMIT to release locks ---------
        self._transition_to_preprocessing(document)
        self.session.commit()

        # --- expensive work, no open transaction --------------------------
        try:
            original = self._load_and_verify_original(document)
            with attempt_temp_dir(self.settings.preprocess_temp_dir or None):
                prepared = preprocess_document(
                    original,
                    media_type=document.media_type,
                    limits=self.limits,
                )
        except PreprocessError as exc:
            self._fail(document, exc.category, exc.message, permanent=exc.permanent)
            if exc.permanent:
                raise job_service.PermanentJobError(exc.message) from exc
            raise

        # --- persist pages idempotently, honoring cancellation ------------
        persisted, failed_page, failure = self._persist_pages(document, prepared)

        # --- finalize state (only if we still own the lease) --------------
        if not self._still_owns_lease():
            # Another worker owns this job now. Leave state to whoever holds
            # the lease; our partial page writes are idempotent and reusable.
            return "lease_lost"

        if failed_page is not None:
            category, detail = failure
            self._fail(document, category, detail, permanent=False, processed=len(persisted))
            # Retryable: let the queue's bounded backoff decide.
            raise RuntimeError(detail)

        if not self._finalize_preprocessed(
            document, total=len(prepared), processed=len(persisted)
        ):
            # Cancelled or deletion started while pages were being persisted.
            # Do not write a terminal state over a document another flow now
            # owns; our partial writes stay idempotent and reusable.
            return "abandoned"
        return f"preprocessed_{len(persisted)}_pages"

    # -- phases -----------------------------------------------------------

    def _transition_to_preprocessing(self, document: SourceDocument) -> None:
        """Validate and move the document into ``preprocessing``."""
        current = state_machine.parse_state(document.processing_state)
        # Normalise through queued so uploaded/failed re-enter the pipeline.
        if current in {S.uploaded, S.failed, S.cancelled}:
            if state_machine.can_transition(current, S.queued):
                state_machine.assert_transition(current, S.queued)
                document.processing_state = S.queued.value
                current = S.queued
        # A redelivered job may already be in preprocessing; that is fine.
        if current == S.preprocessing:
            return
        state_machine.assert_transition(current, S.preprocessing)
        document.processing_state = S.preprocessing.value
        document.error_category = None
        document.error_detail = None
        document.processing_version = PREPROCESSING_VERSION
        self.session.flush()

    def _load_and_verify_original(self, document: SourceDocument) -> bytes:
        """Fetch the immutable original and verify its integrity."""
        if not document.storage_key:
            raise PreprocessError(
                "Document has no stored original", category="storage", permanent=True
            )
        try:
            data = self.store.get(document.storage_key)
        except NotFoundError as exc:
            raise PreprocessError(
                "Original file is missing from storage", category="storage", permanent=True
            ) from exc
        except ObjectStoreError as exc:
            # Transient I/O: worth retrying.
            raise PreprocessError(
                "Failed to read original from storage", category="storage", permanent=False
            ) from exc

        actual = hashlib.sha256(data).hexdigest()
        if actual != document.content_hash:
            # Content no longer matches the recorded identity. Never process
            # changed content under an old document identity.
            raise PreprocessError(
                "Stored original does not match its recorded content hash",
                category="storage",
                permanent=True,
            )
        return data

    def _persist_pages(
        self,
        document: SourceDocument,
        prepared: list[PreparedPage],
    ) -> tuple[list[DocumentPage], Optional[int], tuple[str, str]]:
        """Persist prepared pages idempotently.

        Returns ``(persisted_pages, failed_page_number, (category, detail))``.
        A non-``None`` failed page means processing stopped partway; pages
        already persisted are kept for the next attempt.
        """
        persisted: list[DocumentPage] = []
        for page in prepared:
            # Cooperative cancellation check between pages.
            self.session.expire(document, ["processing_state"])
            state = state_machine.parse_state(document.processing_state)
            if state in {S.cancelled, S.deletion_pending, S.deleted}:
                return persisted, page.page_number, (
                    DocumentErrorCategory.preprocessing.value,
                    f"Processing stopped: document entered {state.value}",
                )

            try:
                row = self._persist_one_page(document, page)
                persisted.append(row)
            except ObjectStoreError:
                return persisted, page.page_number, (
                    DocumentErrorCategory.storage.value,
                    "Failed to write page artifact to storage",
                )
            except Exception as exc:  # noqa: BLE001 - classified, not swallowed
                return persisted, page.page_number, (
                    DocumentErrorCategory.persistence.value,
                    f"Failed to persist page {page.page_number}: {type(exc).__name__}",
                )
            # Commit per page so a crash loses at most one page of work and
            # never holds a transaction across the whole document.
            self.session.commit()
        return persisted, None, ("", "")

    def _persist_one_page(self, document: SourceDocument, page: PreparedPage) -> DocumentPage:
        """Create or update a single page row and its artifact, idempotently."""
        key = generate_page_key(document.id, page.page_number, content_hash=page.content_hash)

        # Artifact write is idempotent: the key is deterministic, so a retry
        # finds the object already present and skips the (immutable) write.
        if not self.store.exists(key):
            self.store.put(key, page.image_png, expected_hash=page.content_hash)

        row = self.session.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == document.id,
                DocumentPage.page_number == page.page_number,
            )
        ).scalar_one_or_none()

        if row is None:
            row = DocumentPage(
                document_id=document.id,
                page_number=page.page_number,
                metadata_json={},
            )
            self.session.add(row)

        # Original-space geometry is the coordinate contract for OCR boxes.
        row.width_px = page.original_width
        row.height_px = page.original_height
        row.working_scale = page.working_scale
        row.processed_storage_key = key
        row.content_hash = page.content_hash
        row.preprocessing_version = PREPROCESSING_VERSION
        row.status = PageStatus.processed.value
        row.failure_category = None
        row.processed_at = datetime.now(timezone.utc)
        row.metadata_json = {
            "working_width": page.working_width,
            "working_height": page.working_height,
            "backend": page.backend,
            "warnings": page.warnings,
        }
        self.session.flush()
        return row

    def _finalize_preprocessed(
        self, document: SourceDocument, *, total: int, processed: int
    ) -> bool:
        """Move preprocessing -> preprocessed (or failed if partial).

        Returns ``False`` — finalizing nothing — when the document left the
        preprocessing lifecycle while this run was working (cancelled, or
        deletion started). The state is re-read here because a deletion is
        committed by a different session and the last per-page check may
        predate it; writing ``preprocessed`` over the top would resurrect a
        document that is on its way out.
        """
        self.session.expire(document, ["processing_state"])
        state = state_machine.parse_state(document.processing_state)
        if state in {S.cancelled, S.deletion_pending, S.deleted}:
            return False

        target = state_machine.mark_preprocessed(
            state,
            total_pages=total,
            processed_pages=processed,
        )
        document.processing_state = target.value
        document.page_count = total
        document.error_category = None
        document.error_detail = None
        record_audit(
            self.session,
            action="document.preprocessed",
            org_id=document.org_id,
            resource_type="source_document",
            resource_id=document.id,
            details={
                "pages": processed,
                "total_pages": total,
                "preprocessing_version": PREPROCESSING_VERSION,
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
        """Record a bounded, non-sensitive failure on the document.

        ``detail`` is a fixed, safe string — never an exception repr or file
        content. The document moves to ``failed``; the retry system decides
        whether to requeue (retryable) or leave it dead (permanent).
        """
        self.session.rollback()
        # Re-read after rollback; the caller's reference may be expired.
        fresh = self.session.get(SourceDocument, document.id)
        if fresh is None:
            return
        current = state_machine.parse_state(fresh.processing_state)
        if current in {S.cancelled, S.deletion_pending, S.deleted}:
            # Do not overwrite an explicit cancellation/deletion with failed.
            return
        target = DocumentErrorCategory.internal.value
        if category in _CATEGORY_MAP:
            target = _CATEGORY_MAP[category]
        if state_machine.can_transition(current, S.failed):
            state_machine.assert_transition(current, S.failed)
            fresh.processing_state = S.failed.value
        fresh.error_category = target
        fresh.error_detail = detail[:500]
        if processed:
            fresh.page_count = processed
        record_audit(
            self.session,
            action="document.processing_failed",
            org_id=fresh.org_id,
            resource_type="source_document",
            resource_id=fresh.id,
            outcome="failure",
            details={"error_category": target, "permanent": permanent},
        )
        # Commit (not just flush) so the failure decision survives the
        # caller's rollback when the error propagates out of the handler.
        self.session.commit()
