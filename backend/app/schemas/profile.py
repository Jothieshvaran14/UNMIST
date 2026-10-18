"""Profile and portfolio schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProfileResponse(BaseModel):
    learner_id: str
    org_id: Optional[str]
    mastery: list[dict]
    retention: list[dict]
    prerequisite_gaps: list[dict]
    assessment_history: list[dict]
    diagnoses: list[dict]
    topics: list[dict]


class PortfolioSettingsResponse(BaseModel):
    public_id: str
    is_public: bool
    headline: Optional[str]
    bio: Optional[str]
    show_assessed_only: bool
    published_at: Optional[datetime]


class PortfolioUpdateRequest(BaseModel):
    is_public: Optional[bool] = None
    headline: Optional[str] = Field(default=None, max_length=300)
    bio: Optional[str] = None
    show_assessed_only: Optional[bool] = None


class PortfolioItemCreateRequest(BaseModel):
    item_type: str = Field(pattern="^(concept|skill|project|assessment|certificate)$")
    title: str = Field(min_length=1, max_length=300)
    concept_id: Optional[str] = None
    evidence_summary: Optional[str] = None
    is_self_reported: bool = False
    visibility: str = Field(default="private", pattern="^(private|public)$")
    score: Optional[float] = None
    max_score: Optional[float] = None


class PortfolioItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    item_type: str
    title: str
    evidence_summary: Optional[str]
    is_self_reported: bool
    visibility: str
    score: Optional[float]
    max_score: Optional[float]


class PublicPortfolioItem(BaseModel):
    item_type: str
    title: str
    evidence_summary: Optional[str]
    is_self_reported: bool
    verification_note: str
    score: Optional[float] = None
    max_score: Optional[float] = None


class PublicProfileResponse(BaseModel):
    public_id: str
    headline: Optional[str]
    bio: Optional[str]
    items: list[PublicPortfolioItem]
    limitations: str
