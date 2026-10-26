"""Purge a document's private storage artifacts and derived rows.

Reached only through ``enqueue_deletion`` (which moves the document to
``deletion_pending``) and the registered ``document.purge`` job. Nothing else
in the codebase calls into this module.

Contract:

  * **Only this document's own data is destroyed.** Shared ``sources`` rows,
    topics, knowledge records, and other tenants' data are never touched, and
    no retention period is invented — the purge runs as soon as the job runs.
  * **Storage first, rows second.** Storage keys live on rows, so the private
    objects are removed before those rows disappear; a crash in between leaves
    the keys in place for the retry to find.
  * **Retry-safe.** An object already gone, or a row already deleted, is a
    success rather than an error, so a re-run converges instead of failing.
  * **A stale worker cannot finalize.** The lease and the ``deletion_pending``
    state are re-checked immediately before the tombstone is written; if either
    changed, this run stops without touching state and lets the new owner
    finish. Deletion is one-way: no path here ever restores a document.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import Settings, get_settings
from ...errors import NotFoundError
from ...models.documents import (
    DocumentPage,
)
from ...models.documents import DocumentProcessingState as S
from ...models.documents import (
    DocumentSegment,
    OcrRegion,
    OcrReview,
    SegmentRegion,
    SegmentReview,
)
from ...models.job import Job
from ...models.knowledge import SourceDocument
from ...services import audit as audit_service
from ...services import jobs as job_service
from ...services.storage import (
    ObjectStore,
    ObjectStoreError,
    get_object_store,
)

# The only state in which a purge may run. Anything else means deletion was
# never requested (or already finished), so a live document is never destroyed.
_PURGE_STATE = S.deletion_pending.value


class DocumentPurgePipeline:
    """Runs one ``document.purge`` job against a single document."""

    def __init__(
        self,
        session: Session,
        job: Job,
        *,
        settings: Optional[Settings] = None,
        store: Optional[ObjectStore] = None,
    ) -> None:
        self.session = session
        self.job = job
        self.settings = settings or get_settings()
        self.store = store or get_object_store(self.settings)
        self._claimed_started_at: Optional[datetime] = job.started_at

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

    # -- storage -----------------------------------------------------------

    def _purge_storage(self, document: SourceDocument) -> int:
        """Remove this document's private objects. Idempotent per key."""
        keys: list[str] = []
        if document.storage_key:
            keys.append(document.storage_key)
        page_keys = self.session.execute(
            select(DocumentPage.processed_storage_key).where(
                DocumentPage.document_id == document.id,
                DocumentPage.processed_storage_key.is_not(None),
            )
        ).scalars()
        keys.extend(k for k in page_keys if k is not None)

        removed = 0
        for key in dict.fromkeys(keys):  # de-duplicate, keep first order
            try:
                if self.store.exists(key):
                    self.store.delete(key)
                    removed += 1
            except NotFoundError:
                # Already gone (a previous attempt got this far): success.
                continue
            except (ObjectStoreError, NotFoundError) as exc:
                # A genuine I/O failure must fail the job so it is retried
                # rather than reported as a completed deletion.
                raise job_service.PermanentJobError(
                    f"Could not remove stored artifact: {type(exc).__name__}"
                ) from exc
        return removed

    # -- derived rows ------------------------------------------------------

    def _purge_rows(self, document: SourceDocument) -> int:
        """Delete this document's derived rows in FK-safe order.

        Written explicitly rather than relying on ON DELETE CASCADE so the
        behaviour is identical on SQLite and PostgreSQL and is directly
        assertable in tests.
        """
        deleted = 0

        # Children of segments first.
        segment_ids = select(DocumentSegment.id).where(
            DocumentSegment.document_id == document.id
        )
        deleted += self.session.query(SegmentReview).filter(
            SegmentReview.segment_id.in_(segment_ids)
        ).delete(synchronize_session=False)
        deleted += self.session.query(SegmentRegion).filter(
            SegmentRegion.segment_id.in_(segment_ids)
        ).delete(synchronize_session=False)
        deleted += (
            self.session.query(DocumentSegment)
            .filter(DocumentSegment.document_id == document.id)
            .delete(synchronize_session=False)
        )

        # Children of regions next (reviews reference regions).
        region_ids = select(OcrRegion.id).where(
            OcrRegion.document_id == document.id
        )
        deleted += self.session.query(OcrReview).filter(
            OcrReview.region_id.in_(region_ids)
        ).delete(synchronize_session=False)
        deleted += (
            self.session.query(OcrRegion)
            .filter(OcrRegion.document_id == document.id)
            .delete(synchronize_session=False)
        )

        # Pages last.
        deleted += (
            self.session.query(DocumentPage)
            .filter(DocumentPage.document_id == document.id)
            .delete(synchronize_session=False)
        )
        self.session.flush()
        return deleted

    # -- entry point -------------------------------------------------------

    def run(self) -> Optional[str]:
        """Execute the job. Returns a short outcome string for logging."""
        document_id = self.job.payload.get("document_id")
        if not document_id:
            raise job_service.PermanentJobError(
                "document.purge job is missing 'document_id'"
            )

        document = self.session.get(SourceDocument, document_id)
        if document is None:
            return "document_missing"

        state = (document.processing_state or "").strip().lower()
        if state == S.deleted.value:
            # Re-delivery after a completed purge: nothing left to do.
            return "already_deleted"
        if state != _PURGE_STATE:
            # Deletion was never requested here, or the row was repurposed.
            # Never destroy a live document from a stale job.
            return f"skipped_state_{state or 'unset'}"

        if not self._still_owns_lease():
            return "lease_lost"

        # --- storage artifacts (before rows: the keys live on the rows) ----
        self._purge_storage(document)

        if not self._still_owns_lease():
            # Another worker owns the job now. Stop with artifacts already
            # removed; the new owner re-runs and converges on the same result.
            return "lease_lost"

        # --- derived rows ---------------------------------------------------
        self._purge_rows(document)

        # --- tombstone, only if we still own it and it is still pending ----
        if not self._still_owns_lease():
            return "lease_lost"
        fresh = self.session.get(SourceDocument, document_id)
        if fresh is None:
            return "document_missing"
        if fresh.processing_state != _PURGE_STATE:
            # The state moved under us; do not write over the new owner's work.
            return f"skipped_state_{fresh.processing_state}"

        fresh.processing_state = S.deleted.value
        # The pointer to the destroyed private object must not outlive it.
        fresh.storage_key = None
        fresh.error_category = None
        fresh.error_detail = None
        fresh.last_job_id = None
        self.session.flush()

        audit_service.record_audit(
            self.session,
            action="document.purged",
            org_id=document.org_id,
            resource_type="source_document",
            resource_id=document.id,
            details={"job_id": str(self.job.id)},
        )
        self.session.commit()
        return "purged"
