"""Learning and assessment schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class QuestionCreateRequest(BaseModel):
    org_id: Optional[str] = None
    topic_id: str
    objective_id: Optional[str] = None
    prompt: str = Field(min_length=1)
    question_type: str = Field(
        default="short_answer",
        pattern="^(short_answer|code|numeric|essay|multiple_choice)$",
    )
    reference_answer: Optional[str] = None
    checks: list[dict] = Field(default_factory=list)


class QuestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    topic_id: str
    objective_id: Optional[str]
    version: int
    prompt: str
    question_type: str
    reference_answer: Optional[str]
    checks: list[dict]
    created_at: datetime


class RubricCriterion(BaseModel):
    id: str
    description: str
    max_points: float = Field(gt=0)
    check: dict = Field(default_factory=dict)
    partial_credit: bool = False
    acceptable_alternatives: list[str] = Field(default_factory=list)


class RubricCreateRequest(BaseModel):
    org_id: Optional[str] = None
    criteria: list[RubricCriterion] = Field(min_length=1)


class RubricResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    question_id: str
    version: int
    criteria: list[dict]
    created_at: datetime


class AttemptCreateRequest(BaseModel):
    question_id: str
    org_id: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


class AttemptSubmitRequest(BaseModel):
    response_text: str = Field(min_length=1)


class CriterionResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    criterion_id: str
    points: float
    max_points: float
    evidence_spans: list[dict]
    explanation: Optional[str]
    confidence: float
    review_status: str


class AttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    question_id: str
    status: str
    score: float
    max_score: float
    rubric_version: Optional[int]
    evaluator_version: str
    submitted_at: Optional[datetime]
    criterion_results: list[CriterionResultResponse] = Field(default_factory=list)


class MasteryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    concept_id: str
    mastery_probability: float
    evidence_count: int
    state: str
    model_version: str
    last_activity_at: Optional[datetime]


class DiagnosisCreateRequest(BaseModel):
    concept_id: str
    org_id: Optional[str] = None


class DiagnosisResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    concept_id: str
    status: str
    hypotheses: list[dict]
    selected_question_id: Optional[str]
    conclusion: Optional[str]
    evidence_summary: Optional[str]
    created_at: datetime


class ReviewSubmitRequest(BaseModel):
    recalled: bool
    quality: int = Field(ge=0, le=5)
    duration_ms: Optional[int] = None


class ReviewResponse(BaseModel):
    schedule_id: str
    status: str
    due_at: datetime
    interval_days: int
    repetitions: int
    lapses: int
