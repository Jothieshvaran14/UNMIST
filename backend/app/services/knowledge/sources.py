"""Source registry and lawful-ingestion enforcement.

Sources are never marked as legally verified when their license or permitted
use is unknown. Ingesting and publishing derived content requires an explicit
permitted (or conditionally permitted) license status.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import ConflictError, NotFoundError, PolicyError
from ...models.knowledge import (
    LicenseStatus,
    Source,
    SourceDocument,
    SourcePassage,
    SourceType,
)

_PERMITTED_LICENSES = {
    LicenseStatus.permitted.value,
    LicenseStatus.permitted_with_conditions.value,
}


@dataclass(frozen=True)
class SourceInput:
    source_type: str
    canonical_url: str
    license_status: str
    publisher: Optional[str] = None
    title: Optional[str] = None
    license_detail: Optional[str] = None
    access_method: str = "api"
    terms_summary: Optional[str] = None


def require_permitted_license(license_status: str, *, url: str) -> None:
    if license_status not in _PERMITTED_LICENSES:
        raise PolicyError(
            "Source license or permitted use is not established; ingestion is blocked. "
            f"url={url} license_status={license_status}",
            code="license_not_permitted",
        )


def get_or_create_source(
    session: Session,
    *,
    org_id: Optional[str],
    source_input: SourceInput,
) -> Source:
    existing = session.execute(
        select(Source).where(
            Source.org_id == org_id,
            Source.canonical_url == source_input.canonical_url,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    if source_input.source_type not in {item.value for item in SourceType}:
        raise ConflictError(f"Unknown source type '{source_input.source_type}'")

    source = Source(
        org_id=org_id,
        source_type=source_input.source_type,
        canonical_url=source_input.canonical_url,
        publisher=source_input.publisher,
        title=source_input.title,
        license_status=source_input.license_status,
        license_detail=source_input.license_detail,
        access_method=source_input.access_method,
        terms_summary=source_input.terms_summary,
    )
    session.add(source)
    session.flush()
    return source


def record_document(
    session: Session,
    *,
    source: Source,
    topic_id: Optional[str],
    url: str,
    media_type: str,
    content: bytes,
    extracted_text: str,
    passages: list[str],
    storage_key: Optional[str] = None,
) -> SourceDocument:
    content_hash = hashlib.sha256(content).hexdigest()
    document = SourceDocument(
        source_id=source.id,
        topic_id=topic_id,
        title=source.title,
        url=url,
        media_type=media_type,
        content_hash=content_hash,
        size_bytes=len(content),
        fetched_at=datetime.now(timezone.utc),
        storage_key=storage_key,
        extracted_text=extracted_text,
        status="extracted",
    )
    session.add(document)
    session.flush()

    for index, passage in enumerate(passages):
        session.add(
            SourcePassage(
                document_id=document.id,
                locator=f"passage:{index}",
                text=passage,
                content_hash=hashlib.sha256(passage.encode("utf-8")).hexdigest(),
            )
        )
    return document


def get_document(session: Session, document_id: str) -> SourceDocument:
    document = session.get(SourceDocument, document_id)
    if document is None:
        raise NotFoundError("Document not found")
    return document
