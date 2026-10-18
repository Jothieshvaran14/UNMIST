"""End-to-end source ingestion: retrieve -> extract -> register -> record.

Licensing is enforced before any network access. Extraction is deterministic
and every recorded passage keeps a stable locator for citation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from ...models.knowledge import Topic
from .extraction import extract_text, split_passages
from .retrieval import RetrievalClient, RetrievedDocument
from .sources import (
    SourceInput,
    get_or_create_source,
    record_document,
    require_permitted_license,
)


@dataclass(frozen=True)
class IngestionRequest:
    url: str
    source_type: str
    license_status: str
    publisher: Optional[str] = None
    title: Optional[str] = None
    license_detail: Optional[str] = None
    access_method: str = "api"
    terms_summary: Optional[str] = None


def ingest_url(
    session: Session,
    *,
    topic: Topic,
    request: IngestionRequest,
    client: RetrievalClient,
) -> dict:
    # Enforce licensing before any network activity.
    require_permitted_license(request.license_status, url=request.url)

    retrieved: RetrievedDocument = client.fetch(request.url)

    base_media_type = retrieved.content_type.split(";", 1)[0].strip().lower()
    text = extract_text(retrieved.content, base_media_type, retrieved.charset)
    if not text.strip():
        raise ValueError("No extractable text was found in the retrieved document")
    passages = split_passages(text)
    if not passages:
        raise ValueError("The retrieved document produced no citable passages")

    source = get_or_create_source(
        session,
        org_id=topic.org_id,
        source_input=SourceInput(
            source_type=request.source_type,
            canonical_url=request.url,
            publisher=request.publisher,
            title=request.title,
            license_status=request.license_status,
            license_detail=request.license_detail,
            access_method=request.access_method,
            terms_summary=request.terms_summary,
        ),
    )
    source.retrieved_at = retrieved.fetched_at
    source.content_hash = retrieved.content_hash
    source.extraction_method = "deterministic-v1"
    source.extraction_version = "1"

    document = record_document(
        session,
        source=source,
        topic_id=topic.id,
        url=retrieved.final_url,
        media_type=base_media_type,
        content=retrieved.content,
        extracted_text=text,
        passages=passages,
    )
    session.flush()
    return {
        "source_id": source.id,
        "document_id": document.id,
        "content_hash": retrieved.content_hash,
        "media_type": base_media_type,
        "passage_count": len(passages),
    }
