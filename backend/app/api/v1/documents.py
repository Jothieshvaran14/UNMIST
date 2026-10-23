"""Secure document upload and retrieval routes.

Uploads are multipart. Processing does **not** start implicitly: an upload is
persisted in the ``uploaded`` state and scheduled through the durable job queue,
but no OCR runs inside the request. Originals are private; the response never
carries a storage key or public URL, and reads are always scoped to the caller's
owner + tenant (IDOR-safe).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...api.deps import client_ip, pagination
from ...db import get_db
from ...errors import (
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
)
from ...models import SourceDocument, User
from ...models.base import utcnow
from ...models.documents import (
    DocumentPage,
)
from ...models.documents import DocumentProcessingState as S
from ...models.documents import (
    DocumentSegment,
    OcrRegion,
    PageStatus,
    ReviewStatus,
    SegmentationStatus,
    SegmentRegion,
    SegmentReview,
)
from ...schemas.common import Page
from ...schemas.documents import (
    DocumentProcessingStatus,
    DocumentResponse,
    OcrRegionResponse,
    SegmentationStatusResponse,
    SegmentRegionRef,
    SegmentResponse,
    SegmentReviewRequest,
    SegmentReviewResponse,
)
from ...security.dependencies import get_current_user, get_membership
from ...services.audit import record_audit
from ...services.documents import ingestion

router = APIRouter(prefix="/documents", tags=["documents"])


def _read_bounded(upload: UploadFile, max_bytes: int) -> bytes:
    """Read the upload in chunks, enforcing the size cap while streaming.

    The cap is applied during reading, so an oversized body is rejected without
    ever being fully buffered in memory.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError(
                f"File exceeds the {max_bytes}-byte upload limit",
                code="file_too_large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _authorize_org_write(session: Session, user: User, org_id: Optional[str]) -> None:
    if org_id is None:
        return
    membership = get_membership(session, user.id, org_id)
    if membership is None:
        raise ValidationError(
            "You are not a member of this organization", code="not_a_member"
        )


def _resolve_org_id(
    session: Session, user: User, org_id: Optional[str]
) -> Optional[str]:
    """Validate an optional org scope for a write, returning it or None."""
    if org_id is None or org_id == "":
        return None
    _authorize_org_write(session, user, org_id)
    return org_id


@router.post(
    "/uploads",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    org_id: Optional[str] = Form(default=None),
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    from ...config import get_settings

    settings = get_settings()
    tenant = _resolve_org_id(session, actor, org_id)

    data = _read_bounded(file, settings.upload_max_bytes)
    document, _validated, created = ingestion.create_upload(
        session,
        data=data,
        filename=file.filename,
        owner_user_id=actor.id,
        org_id=tenant,
        actor_id=actor.id,
        ip_address=client_ip(request),
        settings=settings,
    )
    # Schedule processing only for freshly-ingested documents; a duplicate
    # returns the existing record without re-enqueueing.
    if created:
        ingestion.enqueue_processing(session, document, org_id=tenant)
        record_audit(
            session,
            action="document.enqueue",
            actor_id=actor.id,
            org_id=tenant,
            resource_type="source_document",
            resource_id=document.id,
            details={"job_id": document.last_job_id},
        )
    session.commit()
    return document


@router.get("", response_model=Page[DocumentResponse])
def list_documents(
    limit: int = 20,
    offset: int = 0,
    org_id: Optional[str] = None,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    tenant = _resolve_org_id(session, actor, org_id) if org_id else None
    scope = [
        SourceDocument.owner_user_id == actor.id,
        SourceDocument.org_id == tenant,
    ]
    query = select(SourceDocument).where(*scope)
    total = int(
        session.execute(
            select(func.count()).select_from(query.subquery())
        ).scalar_one()
    )
    page = pagination(limit=limit, offset=offset)
    items = (
        session.execute(
            query.order_by(SourceDocument.created_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        .scalars()
        .all()
    )
    return Page.create(
        [DocumentResponse.model_validate(item) for item in items],
        total,
        page.limit,
        page.offset,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    document = session.get(SourceDocument, document_id)
    # A document with no owner is not an uploaded document and is not
    # addressable through this route; return 404 rather than leak existence.
    if document is None or document.owner_user_id is None:
        raise NotFoundError("Document not found")
    # IDOR defence: the owner must match the caller.
    if document.owner_user_id != actor.id:
        raise NotFoundError("Document not found")
    return document


def _owned_document(
    session: Session, document_id: str, actor: User
) -> SourceDocument:
    """Fetch a document enforcing owner scope (IDOR-safe), or 404."""
    document = session.get(SourceDocument, document_id)
    if document is None or document.owner_user_id is None:
        raise NotFoundError("Document not found")
    if document.owner_user_id != actor.id:
        raise NotFoundError("Document not found")
    return document


@router.get("/{document_id}/processing", response_model=DocumentProcessingStatus)
def get_processing_status(
    document_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Return safe processing progress for an owned document.

    Exposes only lifecycle facts (state, pages processed, retry availability,
    whether preprocessing artifacts are ready). Never leaks storage keys, paths,
    or internal error text beyond the bounded category/detail.
    """
    document = _owned_document(session, document_id, actor)
    state = (
        S(document.processing_state) if document.processing_state else None
    )
    pages_processed = int(
        session.execute(
            select(func.count())
            .select_from(DocumentPage)
            .where(
                DocumentPage.document_id == document.id,
                DocumentPage.status == PageStatus.processed.value,
            )
        ).scalar_one()
    )
    artifacts_ready = state == S.preprocessed
    retry_available = state in {S.failed, S.cancelled}
    return DocumentProcessingStatus(
        id=document.id,
        processing_state=document.processing_state,
        page_count=document.page_count,
        pages_processed=pages_processed,
        artifacts_ready=artifacts_ready,
        retry_available=retry_available,
        error_category=document.error_category,
        error_detail=document.error_detail,
        last_job_id=document.last_job_id,
    )


@router.get(
    "/{document_id}/regions",
    response_model=Page[OcrRegionResponse],
)
def list_ocr_regions(
    document_id: str,
    limit: int = 200,
    offset: int = 0,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Return recognized OCR regions for an owned document.

    Authorization: owner-scoped and server-side (IDOR-safe) — a caller can
    never read another tenant's recognition results. The payload carries text,
    geometry, and model provenance but never a storage key or path.

    Note on what this is *not*: regions are **line-level** recognition results
    from the configured provider. They are not verified text, not a
    reconstruction of the page layout, and not an assessment of correctness.
    """
    document = _owned_document(session, document_id, actor)
    scope = [OcrRegion.document_id == document.id]
    total = int(
        session.execute(
            select(func.count()).select_from(select(OcrRegion).where(*scope).subquery())
        ).scalar_one()
    )
    page = pagination(limit=min(limit, 500), offset=offset)
    rows = (
        session.execute(
            select(OcrRegion)
            .where(*scope)
            .order_by(OcrRegion.page_id.asc(), OcrRegion.reading_order.asc())
            .limit(page.limit)
            .offset(page.offset)
        )
        .scalars()
        .all()
    )
    return Page.create(
        [OcrRegionResponse.model_validate(row, from_attributes=True) for row in rows],
        total,
        page.limit,
        page.offset,
    )


# --- segmentation (Checkpoint E) -------------------------------------------


def _segment_refs(session: Session, segment_ids: list[str]) -> dict[str, list]:
    """Map segment id -> its contributing regions, in segment order.

    Each ref keeps the region's own **page-local** geometry so cross-page
    segments never imply a single shared coordinate system.
    """
    if not segment_ids:
        return {}
    rows = session.execute(
        select(SegmentRegion, OcrRegion, DocumentPage)
        .join(OcrRegion, SegmentRegion.region_id == OcrRegion.id)
        .join(DocumentPage, OcrRegion.page_id == DocumentPage.id)
        .where(SegmentRegion.segment_id.in_(segment_ids))
        .order_by(SegmentRegion.segment_id.asc(), SegmentRegion.position.asc())
    ).all()
    grouped: dict[str, list] = {}
    for link, region, page in rows:
        grouped.setdefault(str(link.segment_id), []).append(
            SegmentRegionRef(
                region_id=str(region.id),
                page_number=int(page.page_number),
                position=int(link.position),
                bbox_left=int(region.bbox_left),
                bbox_top=int(region.bbox_top),
                bbox_width=int(region.bbox_width),
                bbox_height=int(region.bbox_height),
                text=region.text,
                review_status=region.review_status,
            )
        )
    return grouped


def _segment_to_response(row: DocumentSegment, refs: list) -> SegmentResponse:
    return SegmentResponse(
        id=str(row.id),
        document_id=str(row.document_id),
        parent_id=str(row.parent_id) if row.parent_id else None,
        segment_type=row.segment_type,
        label=row.label,
        start_page=row.start_page,
        end_page=row.end_page,
        reading_order=row.reading_order,
        bbox_left=row.bbox_left,
        bbox_top=row.bbox_top,
        bbox_width=row.bbox_width,
        bbox_height=row.bbox_height,
        text=row.text,
        effective_text=row.corrected_text if row.corrected_text else row.text,
        corrected_text=row.corrected_text,
        uncertainty=row.uncertainty,
        algorithm=row.algorithm,
        algorithm_version=row.algorithm_version,
        review_status=row.review_status,
        reviewed_by=str(row.reviewed_by) if row.reviewed_by else None,
        reviewed_at=row.reviewed_at,
        region_count=len(refs),
        region_refs=refs,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _segmentation_status(
    session: Session, document: SourceDocument, *, available: bool
) -> SegmentationStatusResponse:
    total = int(
        session.execute(
            select(func.count())
            .select_from(DocumentSegment)
            .where(DocumentSegment.document_id == document.id)
        ).scalar_one()
    )
    pending = int(
        session.execute(
            select(func.count())
            .select_from(DocumentSegment)
            .where(
                DocumentSegment.document_id == document.id,
                DocumentSegment.review_status == ReviewStatus.pending.value,
            )
        ).scalar_one()
    )
    return SegmentationStatusResponse(
        id=str(document.id),
        segmentation_status=document.segmentation_status,
        segmentation_version=document.segmentation_version,
        segmentation_error_category=document.segmentation_error_category,
        segmentation_error_detail=document.segmentation_error_detail,
        last_segment_job_id=(
            str(document.last_segment_job_id) if document.last_segment_job_id else None
        ),
        segmentation_run=int(document.segmentation_run or 0),
        segment_available=available,
        total_segments=total,
        pending_review=pending,
        reviewed=total - pending,
    )


@router.post(
    "/{document_id}/segment",
    response_model=SegmentationStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_segmentation(
    document_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Request segmentation for an owned, already-recognized document.

    Authorization: owner-scoped and server-side (IDOR-safe). Eligibility is
    checked here so an un-segmentable document returns a clean 409 instead of
    scheduling a job that can only fail. The response reports the independent
    ``segmentation_status`` axis — ``processing_state`` (OCR) is untouched.

    Re-requesting while a job is in flight is idempotent (the existing job is
    returned). Segments themselves are idempotent by deterministic uid, so a
    re-run updates rather than duplicates and never overwrites reviewer
    corrections.
    """
    document = _owned_document(session, document_id, actor)
    job = ingestion.enqueue_segmentation(session, document, org_id=document.org_id)
    record_audit(
        session,
        action="document.segment_requested",
        actor_id=actor.id,
        org_id=document.org_id,
        resource_type="source_document",
        resource_id=document.id,
        details={"job_id": str(job.id), "run": int(document.segmentation_run or 0)},
    )
    session.commit()
    return _segmentation_status(session, document, available=False)


@router.get("/{document_id}/segmentation", response_model=SegmentationStatusResponse)
def get_segmentation_status(
    document_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Return safe segmentation status for an owned document.

    Never exposes storage keys, paths, or internal error text beyond the
    bounded category/detail already stored on the document.
    """
    document = _owned_document(session, document_id, actor)
    eligible = document.segmentation_status in {
        SegmentationStatus.not_run.value,
        SegmentationStatus.failed.value,
        SegmentationStatus.partial.value,
        SegmentationStatus.completed.value,
        SegmentationStatus.cancelled.value,
    }
    return _segmentation_status(session, document, available=eligible)


@router.get("/{document_id}/segments", response_model=Page[SegmentResponse])
def list_segments(
    document_id: str,
    limit: int = 50,
    offset: int = 0,
    segment_type: Optional[str] = None,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Return question/answer candidates for an owned document, in reading order.

    Each item carries its contributing source regions with page-local geometry,
    so a reviewer can trace assembled text back to its evidence. This is
    *candidate structure with categorical uncertainty*, not verified content and
    never an assessment of correctness.
    """
    document = _owned_document(session, document_id, actor)
    scope = [DocumentSegment.document_id == document.id]
    if segment_type:
        scope.append(DocumentSegment.segment_type == segment_type)
    total = int(
        session.execute(
            select(func.count()).select_from(select(DocumentSegment).where(*scope).subquery())
        ).scalar_one()
    )
    page = pagination(limit=min(limit, 200), offset=offset)
    rows = (
        session.execute(
            select(DocumentSegment)
            .where(*scope)
            .order_by(DocumentSegment.reading_order.asc(), DocumentSegment.id.asc())
            .limit(page.limit)
            .offset(page.offset)
        )
        .scalars()
        .all()
    )
    refs = _segment_refs(session, [str(r.id) for r in rows])
    items = [
        _segment_to_response(row, refs.get(str(row.id), [])) for row in rows
    ]
    return Page.create(items, total, page.limit, page.offset)


@router.post(
    "/{document_id}/segments/{segment_id}/review",
    response_model=SegmentReviewResponse,
)
def review_segment(
    document_id: str,
    segment_id: str,
    payload: SegmentReviewRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Record a human confirmation or correction of one segment.

    Authorization: owner-scoped (IDOR-safe) and enforced server-side. The
    mutation is append-only in ``segment_reviews`` and flags the segment as no
    longer ``pending``, which is what makes reprocessing preserve it: a later
    run may update machine segments but never overwrites this one.

    ``action="confirm"`` records structure a human verified; ``action="correct"``
    additionally replaces the effective text. Neither implies the answer is
    *correct* — that is assessment, out of scope for this milestone.
    """
    document = _owned_document(session, document_id, actor)
    segment = session.get(DocumentSegment, segment_id)
    # 404 (not 403) so segment ids cannot be probed across tenants.
    if segment is None or str(segment.document_id) != document.id:
        raise NotFoundError("Segment not found")

    action = (payload.action or "").strip().lower()
    if action not in {"confirm", "correct"}:
        raise ValidationError(
            "action must be 'confirm' or 'correct'", details={"field": "action"}
        )
    corrected: Optional[str] = None
    if action == "correct":
        corrected = (payload.corrected_text or "").strip()
        if not corrected:
            raise ValidationError(
                "corrected_text is required when action is 'correct'",
                details={"field": "corrected_text"},
            )
        if len(corrected) > 20000:
            raise ValidationError(
                "corrected_text exceeds the 20000-character limit",
                details={"field": "corrected_text"},
            )

    previous = session.execute(
        select(func.count())
        .select_from(SegmentReview)
        .where(SegmentReview.segment_id == segment.id)
    ).scalar_one()
    revision = int(previous) + 1
    effective_before = segment.corrected_text or segment.text

    session.add(
        SegmentReview(
            segment_id=segment.id,
            revision=revision,
            previous_text=effective_before,
            corrected_text=corrected,
            reason=(payload.reason or "").strip()[:500] or None,
            reviewer_id=actor.id,
        )
    )
    segment.review_status = (
        ReviewStatus.corrected.value
        if action == "correct"
        else ReviewStatus.confirmed.value
    )
    segment.reviewed_by = actor.id
    segment.reviewed_at = utcnow()
    if action == "correct":
        segment.corrected_text = corrected

    record_audit(
        session,
        action="document.segment_review",
        actor_id=actor.id,
        org_id=document.org_id,
        resource_type="document_segment",
        resource_id=str(segment.id),
        details={
            "review_action": action,
            "revision": revision,
            "segment_type": segment.segment_type,
            "uncertainty": segment.uncertainty,
        },
    )
    session.commit()
    return SegmentReviewResponse(
        segment_id=str(segment.id),
        revision=revision,
        review_status=segment.review_status,
        effective_text=segment.corrected_text or segment.text,
        reviewed_at=segment.reviewed_at or segment.updated_at,
    )
