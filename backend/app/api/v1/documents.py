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
from ...errors import NotFoundError, PayloadTooLargeError, ValidationError
from ...models import SourceDocument, User
from ...schemas.common import Page
from ...schemas.documents import DocumentResponse
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
