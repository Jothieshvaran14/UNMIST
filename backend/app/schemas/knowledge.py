"""Knowledge-domain request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class TopicCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: Optional[str] = None


class TopicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    description: Optional[str]
    org_id: Optional[str]
    is_public: bool
    created_at: datetime
    created_by: Optional[str]


class SourceIngestRequest(BaseModel):
    url: str = Field(min_length=5, max_length=2048)
    source_type: str = Field(
        pattern=(
            "^(documentation|standard|academic_metadata|academic_paper|"
            "course_material|textbook|user_upload|web)$"
        )
    )
    license_status: str = Field(
        pattern="^(permitted|permitted_with_conditions|unknown|restricted|not_permitted)$"
    )
    publisher: Optional[str] = None
    title: Optional[str] = None
    license_detail: Optional[str] = None
    access_method: str = "api"
    terms_summary: Optional[str] = None


class SourceIngestResponse(BaseModel):
    source_id: str
    document_id: str
    content_hash: str
    media_type: str
    passage_count: int


class GraphBuildResponse(BaseModel):
    build_id: str
    job_id: str
    status: str


class GraphBuildStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    trigger: str
    error: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    created_at: datetime


class GraphVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version: int
    status: str
    concept_count: int
    relationship_count: int
    validation_report: dict[str, Any]
    created_at: datetime


class RollbackRequest(BaseModel):
    target_version: int = Field(ge=1)
