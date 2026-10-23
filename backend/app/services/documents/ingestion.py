"""Secure document ingestion: validate, store privately, persist, enqueue.

The flow is deliberately ordered so that nothing untrusted is persisted or
scheduled before it has been validated:

    validate bytes  ->  store privately  ->  persist metadata  ->  enqueue job

Failure at any step leaves no half-ingested document behind. The durable job
``document.process`` is registered with an idempotency key derived from the
document id, so a retried upload cannot schedule the same document twice.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import Settings, get_settings
from ...errors import ConflictError, NotFoundError, PolicyError
from ...models.documents import DocumentProcessingState
from ...models.job import Job, JobStatus
from ...models.knowledge import (
    LicenseStatus,
    Source,
    SourceDocument,
    SourceType,
)
from ...services import jobs as job_service
from ...services.audit import record_audit
from ...services.storage import (
    ObjectStore,
    generate_document_key,
    get_object_store,
)
from .validation import ValidatedUpload, validate_upload

# Job type registered by the processing worker (Checkpoint C).
DOCUMENT_PROCESS_JOB = "document.process"
# Job type registered by the OCR worker (Checkpoint D). Separate from
# preprocessing so OCR's optional ML dependency and different failure profile
# never block plain page preparation.
DOCUMENT_OCR_JOB = "document.ocr"
# Job type registered by the segmentation worker (Checkpoint E). Separate from
# OCR so structure extraction can be requested, re-run, and fail independently
# of recognition, without touching the OCR state machine.
DOCUMENT_SEGMENT_JOB = "document.segment"
# Job type registered by the storage-purge worker (Checkpoint G). Separate from
# processing: it is the only job that destroys data, and it must never share a
# lease, a retry budget, or a code path with the jobs that create artifacts.
DOCUMENT_PURGE_JOB = "document.purge"

# One private source row is reused per tenant for all user uploads, so upload
# provenance stays lawful (explicit permitted status) and easy to audit.
_UPLOAD_SOURCE_URL = "urn:unmist:user-uploads"


def _upload_source(session: Session, org_id: Optional[str]) -> Source:
    """Get or create the tenant's user-upload source with a permitted license."""
    existing = session.execute(
        select(Source).where(
            Source.org_id == org_id,
            Source.canonical_url == _UPLOAD_SOURCE_URL,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    source = Source(
        org_id=org_id,
        source_type=SourceType.user_upload.value,
        canonical_url=_UPLOAD_SOURCE_URL,
        publisher="User upload",
        title="User uploads",
        # User-uploaded content is the tenant's own material; its lawful use is
        # the tenant's responsibility and is recorded as permitted.
        license_status=LicenseStatus.permitted.value,
        license_detail="Tenant-uploaded content; usage rights are the tenant's responsibility.",
        access_method="upload",
    )
    session.add(source)
    session.flush()
    return source


def count_documents(
    session: Session, *, owner_user_id: str, org_id: Optional[str]
) -> int:
    """Count a principal's existing documents, for the per-user quota."""
    query = select(func.count(SourceDocument.id)).where(
        SourceDocument.owner_user_id == owner_user_id,
        SourceDocument.org_id == org_id,
        SourceDocument.processing_state != DocumentProcessingState.deleted.value,
    )
    return int(session.execute(query).scalar_one())


def create_upload(
    session: Session,
    *,
    data: bytes,
    filename: Optional[str],
    owner_user_id: str,
    org_id: Optional[str],
    actor_id: str,
    ip_address: Optional[str] = None,
    settings: Optional[Settings] = None,
    store: Optional[ObjectStore] = None,
) -> tuple[SourceDocument, ValidatedUpload, bool]:
    """Ingest an uploaded document.

    Returns ``(document, validation, created)``. ``created`` is False when an
    identical document (same owner and content hash) already existed, in which
    case the existing record is returned and nothing new was stored.
    """
    cfg = settings or get_settings()
    validated = validate_upload(data, filename, settings=cfg)

    # Duplicate handling by content hash: the same owner re-uploading the same
    # bytes gets the existing document back rather than a second stored copy.
    existing = session.execute(
        select(SourceDocument).where(
            SourceDocument.owner_user_id == owner_user_id,
            SourceDocument.org_id == org_id,
            SourceDocument.content_hash == validated.content_hash,
            SourceDocument.processing_state != DocumentProcessingState.deleted.value,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, validated, False

    quota = count_documents(session, owner_user_id=owner_user_id, org_id=org_id)
    if quota >= cfg.upload_max_per_user:
        raise PolicyError(
            f"Per-user document limit of {cfg.upload_max_per_user} reached",
            code="document_quota_exceeded",
        )

    store = store or get_object_store(cfg)

    # We need a document id before we can build the storage key, so create the
    # row first (it has an id) but persist only after the bytes are stored.
    source = _upload_source(session, org_id)
    document = SourceDocument(
        source_id=source.id,
        topic_id=None,
        owner_user_id=owner_user_id,
        org_id=org_id,
        original_filename=validated.display_filename,
        processing_state=DocumentProcessingState.uploaded.value,
        page_count=validated.page_count,
        title=validated.display_filename,
        url=None,
        media_type=validated.media_type,
        content_hash=validated.content_hash,
        size_bytes=validated.size_bytes,
        storage_key=None,
        status="pending",
    )
    session.add(document)
    session.flush()

    key = generate_document_key(document.id, kind="originals")
    # Hash is verified by the store before the bytes are accepted.
    store.put(key, data, expected_hash=validated.content_hash)
    document.storage_key = key

    record_audit(
        session,
        action="document.upload",
        actor_id=actor_id,
        org_id=org_id,
        resource_type="source_document",
        resource_id=document.id,
        details={
            "media_type": validated.media_type,
            "size_bytes": validated.size_bytes,
            "page_count": validated.page_count,
            "content_hash": validated.content_hash,
            "original_filename": validated.display_filename,
        },
        ip_address=ip_address,
    )
    session.flush()
    return document, validated, True


def enqueue_processing(
    session: Session,
    document: SourceDocument,
    *,
    org_id: Optional[str] = None,
) -> Job:
    """Schedule the document for processing (idempotent per document).

    Enqueueing also moves the document into ``queued`` through the state
    machine, so the persisted document state always reflects that a job exists
    for it. Re-enqueueing a document already in flight returns the existing job
    rather than scheduling a duplicate.
    """
    from ...models.documents import DocumentProcessingState as S
    from . import state as state_machine

    current = S(document.processing_state)
    # Deletion is one-way; a stale upload/retry must not restart processing on
    # a document that is going away.
    if current in {S.deletion_pending, S.deleted}:
        raise ConflictError(
            "Document is being deleted",
            code="document_deletion_pending",
            details={"processing_state": current.value},
        )
    if current in {S.queued, S.preprocessing, S.preprocessed, S.ocr_processing}:
        # Already in flight or already prepared; do not double-schedule.
        if document.last_job_id:
            existing = session.get(Job, document.last_job_id)
            if existing is not None and existing.status in {
                JobStatus.pending.value,
                JobStatus.running.value,
            }:
                return existing

    # A cancelled or failed document re-enters through queued; an uploaded one
    # moves uploaded -> queued. Both are validated against the graph.
    if state_machine.can_transition(current, S.queued):
        state_machine.assert_transition(current, S.queued)
        document.processing_state = S.queued.value

    job = job_service.enqueue(
        session,
        job_type=DOCUMENT_PROCESS_JOB,
        payload={
            "document_id": document.id,
            "org_id": org_id,
            "content_hash": document.content_hash,
        },
        tenant_id=org_id,
        idempotency_key=f"{DOCUMENT_PROCESS_JOB}:{document.id}",
    )
    document.last_job_id = job.id
    session.flush()
    return job


def _active_job(session: Session, job_id: Optional[str]) -> Optional[Job]:
    """Return the referenced job only while it is still pending or running."""
    if not job_id:
        return None
    existing = session.get(Job, job_id)
    if existing is None:
        return None
    if existing.status not in {JobStatus.pending.value, JobStatus.running.value}:
        return None
    return existing


def _prepare_pages_exist(session: Session, document: SourceDocument) -> bool:
    """True once preprocessing has persisted at least one page artifact."""
    from ...models.documents import DocumentPage

    return bool(
        session.execute(
            select(DocumentPage.id)
            .where(DocumentPage.document_id == document.id)
            .limit(1)
        ).first()
    )


def _next_run(session: Session, document: SourceDocument, job_type: str) -> int:
    """Return the next run number for ``job_type`` on this document.

    Derived from how many jobs of that type already exist for the document
    rather than a stored counter, so it needs no extra column and stays correct
    after a partial failure. Concurrent requests can compute the same number,
    but the queue's unique ``idempotency_key`` then de-duplicates them into a
    single job — which is the behaviour we want.
    """
    prefix = f"{job_type}:{document.id}:"
    existing = session.execute(
        select(func.count())
        .select_from(Job)
        .where(Job.job_type == job_type, Job.idempotency_key.like(f"{prefix}%"))
    ).scalar_one()
    return int(existing) + 1


def enqueue_ocr(
    session: Session,
    document: SourceDocument,
    *,
    org_id: Optional[str] = None,
) -> Job:
    """Schedule OCR for a prepared document (idempotent per run).

    Eligibility is decided here rather than left to the worker, so an
    unrecognizable document gets a clean ``409`` instead of a job that can only
    fail:

      * ``preprocessed`` may enter OCR for the first time;
      * ``review_required`` / ``failed`` may re-enter it (recognition did not
        finish, and the state graph permits re-entering ``ocr_processing``);
      * ``queued`` / ``preprocessing`` / ``ocr_processing`` are already in
        flight, so an active job is returned instead of scheduling a duplicate;
      * anything else — including ``completed``, which is terminal for
        processing, and ``deletion_pending`` / ``deleted`` — is refused.

    Each scheduling request takes a fresh run number, so the idempotency key is
    run-suffixed exactly like segmentation: a reprocessing request can never be
    silently answered by an old ``succeeded`` job. Within one run, repeated or
    concurrent requests still de-duplicate onto the same job.
    """
    from ...models.documents import DocumentProcessingState as S
    from . import state as state_machine

    current = S(document.processing_state)

    # In flight: hand back the job that is already doing this work.
    if current in {S.queued, S.preprocessing, S.ocr_processing}:
        existing = _active_job(session, document.last_job_id)
        if existing is not None:
            return existing

    # Deletion is one-way: no new recognition may start on a document that is
    # going away, and a stale request must not resurrect it.
    if current in {S.deletion_pending, S.deleted}:
        raise ConflictError(
            "Document is being deleted",
            code="document_deletion_pending",
            details={"processing_state": current.value},
        )

    if current not in {S.preprocessed, S.review_required, S.failed}:
        raise ConflictError(
            "Document is not ready for OCR",
            details={"processing_state": current.value, "hint": "run preprocessing first"},
        )
    if not _prepare_pages_exist(session, document):
        raise ConflictError(
            "Document has no preprocessed pages to recognize",
            details={"hint": "run preprocessing first"},
        )

    state_machine.assert_transition(current, S.ocr_processing)
    document.processing_state = S.ocr_processing.value

    run = _next_run(session, document, DOCUMENT_OCR_JOB)
    job = job_service.enqueue(
        session,
        job_type=DOCUMENT_OCR_JOB,
        payload={
            "document_id": document.id,
            "org_id": org_id,
            "content_hash": document.content_hash,
            "run": run,
        },
        tenant_id=org_id,
        idempotency_key=f"{DOCUMENT_OCR_JOB}:{document.id}:{run}",
    )
    document.last_job_id = job.id
    session.flush()
    return job


def enqueue_deletion(
    session: Session,
    document: SourceDocument,
    *,
    org_id: Optional[str] = None,
) -> Job:
    """Begin deleting an owned document (idempotent, one-way).

    Moves the document to ``deletion_pending`` — a state every pipeline treats
    as a skip, so no new preprocessing, OCR or segmentation work starts from
    here on, and a worker already mid-run stops rather than finishing and
    writing a terminal state over the top.

    The returned job destroys this document's own storage artifacts and derived
    rows. Deletion only becomes ``deleted`` once that cleanup has actually
    finished, so a failed purge is retried rather than reported as done.
    Calling this again while a purge is in flight returns the existing job.
    """
    from ...models.documents import DocumentProcessingState as S
    from . import state as state_machine

    current = S(document.processing_state)

    # Terminal: nothing left to do, and nothing to schedule.
    if current == S.deleted:
        raise ConflictError(
            "Document has already been deleted",
            code="document_deleted",
            details={"processing_state": current.value},
        )

    # Already underway: do not schedule a second purge for the same document.
    if current == S.deletion_pending:
        existing = _active_job(session, document.last_job_id)
        if existing is not None and existing.job_type == DOCUMENT_PURGE_JOB:
            return existing

    state_machine.assert_transition(current, S.deletion_pending)
    document.processing_state = S.deletion_pending.value

    run = _next_run(session, document, DOCUMENT_PURGE_JOB)
    job = job_service.enqueue(
        session,
        job_type=DOCUMENT_PURGE_JOB,
        payload={
            "document_id": document.id,
            "org_id": org_id,
            "run": run,
        },
        tenant_id=org_id,
        idempotency_key=f"{DOCUMENT_PURGE_JOB}:{document.id}:{run}",
    )
    document.last_job_id = job.id
    session.flush()
    return job


def enqueue_segmentation(
    session: Session,
    document: SourceDocument,
    *,
    org_id: Optional[str] = None,
) -> Job:
    """Schedule segmentation for a recognized document (idempotent per run).

    Segmentation reads persisted OCR regions, so it is refused until OCR has
    produced some (the pipeline enforces that too, but rejecting here gives a
    clean 409 instead of a failed job). ``processing_state`` is deliberately
    untouched: segmentation runs on its own status axis and must not disturb
    the OCR lifecycle, where ``completed`` is terminal.

    Scheduling is idempotent while a job is in flight — a second request
    returns the existing job. Once no job is in flight, each request starts a
    new numbered run (``segmentation_run``), so reprocessing and
    algorithm-version upgrades can be requested explicitly; segment identity is
    idempotent either way, so a re-run updates rather than duplicates.
    """
    from ...models.documents import OcrRegion as _OcrRegion
    from ...models.documents import SegmentationStatus

    if document.segmentation_status in {
        SegmentationStatus.scheduled.value,
        SegmentationStatus.processing.value,
    } and document.last_segment_job_id:
        existing = session.get(Job, document.last_segment_job_id)
        if existing is not None and existing.status in {
            JobStatus.pending.value,
            JobStatus.running.value,
        }:
            return existing

    state = (document.processing_state or "").strip().lower()
    if state in {"uploaded", "queued", "preprocessing", "failed", "cancelled",
                 "deletion_pending", "deleted"}:
        raise ConflictError(
            "Document is not ready for segmentation",
            details={"processing_state": state},
        )

    has_regions = bool(
        session.execute(
            select(_OcrRegion.id).where(_OcrRegion.document_id == document.id).limit(1)
        ).first()
    )
    if not has_regions:
        raise ConflictError(
            "Document has no recognized text to segment yet",
            details={"hint": "run OCR first"},
        )

    document.segmentation_run = int(document.segmentation_run or 0) + 1
    job = job_service.enqueue(
        session,
        job_type=DOCUMENT_SEGMENT_JOB,
        payload={
            "document_id": document.id,
            "org_id": org_id,
            "run": document.segmentation_run,
        },
        tenant_id=org_id,
        idempotency_key=(
            f"{DOCUMENT_SEGMENT_JOB}:{document.id}:{document.segmentation_run}"
        ),
    )
    document.last_segment_job_id = job.id
    document.segmentation_status = SegmentationStatus.scheduled.value
    document.segmentation_error_category = None
    document.segmentation_error_detail = None
    session.flush()
    return job


def get_owned_document(
    session: Session,
    document_id: str,
    *,
    owner_user_id: str,
    org_id: Optional[str],
) -> SourceDocument:
    """Fetch a document, enforcing owner and tenant scope (IDOR-safe)."""
    document = session.get(SourceDocument, document_id)
    if document is None or document.owner_user_id is None:
        raise NotFoundError("Document not found")
    if document.owner_user_id != owner_user_id:
        raise NotFoundError("Document not found")
    if document.org_id != org_id:
        raise NotFoundError("Document not found")
    return document
