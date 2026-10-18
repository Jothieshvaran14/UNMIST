"""Learning and assessment models: questions, rubrics, attempts, mastery."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from ..db import Base
from .base import GUID, IdMixin, TimestampMixin, UTCDateTime


class Question(IdMixin, TimestampMixin, Base):
    __tablename__ = "questions"

    topic_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    objective_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("learning_objectives.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(40), default="short_answer", nullable=False)
    reference_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Deterministic validators: [{"kind": "numeric", ...}, {"kind": "code", ...}]
    checks: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Rubric(IdMixin, TimestampMixin, Base):
    __tablename__ = "rubrics"

    question_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("questions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # criteria: [{id, description, max_points, partial_credit, acceptable_alternatives,
    #             evidence_required, check}]
    criteria: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (UniqueConstraint("question_id", "version", name="uq_rubric"),)


class AssessmentAttempt(IdMixin, TimestampMixin, Base):
    __tablename__ = "assessment_attempts"

    question_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("questions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    learner_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    org_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    response_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="in_progress", nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rubric_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    evaluator_version: Mapped[str] = mapped_column(
        String(60), default="deterministic-v1", nullable=False
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)


class CriterionResult(IdMixin, TimestampMixin, Base):
    __tablename__ = "criterion_results"

    attempt_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("assessment_attempts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    criterion_id: Mapped[str] = mapped_column(String(60), nullable=False)
    points: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_points: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_spans: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    review_status: Mapped[str] = mapped_column(String(30), default="auto", nullable=False)
    reviewer_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class MasteryState(str, Enum):
    never_assessed = "never_assessed"
    learning = "learning"
    demonstrated = "demonstrated"
    retained = "retained"
    at_risk = "at_risk"
    failing = "failing"
    requires_reassessment = "requires_reassessment"
    insufficient_evidence = "insufficient_evidence"


class MasteryEstimate(IdMixin, TimestampMixin, Base):
    __tablename__ = "mastery_estimates"

    learner_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    concept_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("concepts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    org_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    mastery_probability: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    model_version: Mapped[str] = mapped_column(
        String(60), default="bkt-provisional-v1", nullable=False
    )
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[str] = mapped_column(
        String(40), default=MasteryState.never_assessed.value, nullable=False
    )
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )

    __table_args__ = (
        UniqueConstraint("learner_id", "concept_id", "org_id", name="uq_mastery"),
    )


class Diagnosis(IdMixin, TimestampMixin, Base):
    """A prerequisite-gap diagnosis with competing hypotheses."""

    __tablename__ = "diagnoses"

    learner_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    concept_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("concepts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    org_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    hypotheses: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    selected_question_id: Mapped[Optional[str]] = mapped_column(GUID, nullable=True)
    conclusion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    evidence_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
