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
from ...errors import NotFoundError, PolicyError
from ...models.documents import DocumentProcessingState
from ...models.job import Job
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
    """Schedule the document for processing (idempotent per document)."""
    from ...models.documents import DocumentProcessingState as S

    state = S(document.processing_state)
    if state in {S.queued, S.preprocessing, S.ocr_processing}:
        # Already in flight; do not double-schedule.
        if document.last_job_id:
            existing = session.get(Job, document.last_job_id)
            if existing is not None:
                return existing

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
