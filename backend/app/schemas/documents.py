"""Document-ingestion request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    """A persisted uploaded document.

    Deliberately exposes no storage key and no signed URL: originals are private
    and are only ever served through an authorized API path, never a public link.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_user_id: Optional[str]
    org_id: Optional[str]
    original_filename: Optional[str]
    media_type: str
    content_hash: str
    size_bytes: int
    page_count: int
    processing_state: Optional[str]
    error_category: Optional[str]
    error_detail: Optional[str]
    last_job_id: Optional[str]
    created_at: datetime
    updated_at: datetime
