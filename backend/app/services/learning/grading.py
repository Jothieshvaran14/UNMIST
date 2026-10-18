"""RubricLens grading service.

Grading is criterion-level and deterministic. Every decision preserves the
rubric version, evaluator version, evidence spans, confidence, and review flag.
No model judgment is used; subjective criteria are flagged for human review.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import ConflictError, NotFoundError, ValidationError
from ...models.learning import AssessmentAttempt, CriterionResult, Question, Rubric

EVALUATOR_VERSION = "deterministic-rubriclens-v1"


def get_question(session: Session, question_id: str) -> Question:
    question = session.get(Question, question_id)
    if question is None:
        raise NotFoundError("Question not found")
    return question


def get_latest_rubric(session: Session, question_id: str) -> Rubric:
    rubric = session.execute(
        select(Rubric)
        .where(Rubric.question_id == question_id)
        .order_by(Rubric.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if rubric is None:
        raise NotFoundError("No rubric exists for this question")
    return rubric


def create_attempt(
    session: Session,
    *,
    question_id: str,
    learner_id: str,
    org_id: Optional[str],
    idempotency_key: Optional[str] = None,
) -> AssessmentAttempt:
    if idempotency_key:
        existing = session.execute(
            select(AssessmentAttempt).where(
                AssessmentAttempt.question_id == question_id,
                AssessmentAttempt.learner_id == learner_id,
                AssessmentAttempt.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    attempt = AssessmentAttempt(
        question_id=question_id,
        learner_id=learner_id,
        org_id=org_id,
        status="in_progress",
        idempotency_key=idempotency_key,
    )
    session.add(attempt)
    session.flush()
    return attempt


def grade_response(
    session: Session,
    *,
    attempt: AssessmentAttempt,
    rubric: Rubric,
    response_text: str,
) -> list[CriterionResult]:
    decisions: list[CriterionResult] = []
    criteria = rubric.criteria or []
    if not criteria:
        raise ValidationError("Rubric has no criteria")

    total_points = 0.0
    total_max = 0.0

    for criterion in criteria:
        criterion_id = str(criterion.get("id"))
        max_points = float(criterion.get("max_points", 0))
        check = criterion.get("check") or {}
        from .checks import run_check

        result = run_check(response_text, check)
        points = max_points if result.passed else 0.0
        if not result.passed and criterion.get("partial_credit") and result.evidence:
            points = round(max_points * 0.5, 2)

        total_points += points
        total_max += max_points

        decisions.append(
            CriterionResult(
                attempt_id=attempt.id,
                criterion_id=criterion_id,
                points=points,
                max_points=max_points,
                evidence_spans=[result.evidence] if result.evidence else [],
                explanation=result.detail,
                confidence=result.confidence,
                review_status="review" if result.requires_review else "auto",
            )
        )

    attempt.score = round(total_points, 4)
    attempt.max_score = round(total_max, 4)
    attempt.status = "graded"
    attempt.rubric_version = rubric.version
    attempt.evaluator_version = EVALUATOR_VERSION
    attempt.submitted_at = datetime.now(timezone.utc)
    attempt.response_text = response_text
    for decision in decisions:
        session.add(decision)
    session.flush()
    return decisions


def submit_attempt(
    session: Session,
    *,
    attempt: AssessmentAttempt,
    response_text: str,
) -> AssessmentAttempt:
    if attempt.status == "graded":
        raise ConflictError("This attempt has already been graded")
    if not response_text or not response_text.strip():
        raise ValidationError("A non-empty response is required")
    rubric = get_latest_rubric(session, attempt.question_id)
    grade_response(session, attempt=attempt, rubric=rubric, response_text=response_text)
    return attempt
