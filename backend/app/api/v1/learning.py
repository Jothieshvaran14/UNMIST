"""LearnTrace + RubricLens API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db import get_db
from ...errors import AuthorizationError, NotFoundError
from ...models.knowledge import LearningObjective
from ...models.learning import (
    AssessmentAttempt,
    CriterionResult,
    Question,
    Rubric,
)
from ...models.user import User
from ...schemas.learning import (
    AttemptCreateRequest,
    AttemptResponse,
    AttemptSubmitRequest,
    CriterionResultResponse,
    DiagnosisCreateRequest,
    DiagnosisResponse,
    MasteryResponse,
    QuestionCreateRequest,
    QuestionResponse,
    ReviewResponse,
    ReviewSubmitRequest,
    RubricCreateRequest,
    RubricResponse,
)
from ...security.dependencies import get_current_user
from ...services.audit import record_audit
from ...services.knowledge import graph_service
from ...services.learning import diagnosis as diagnosis_service
from ...services.learning import grading as grading_service
from ...services.learning import mastery as mastery_service
from ...services.learning import progress as progress_service
from ...services.retention import scheduling as retention_service

router = APIRouter(tags=["learning"])


# ---------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------
@router.post("/questions", response_model=QuestionResponse, status_code=201)
def create_question(
    payload: QuestionCreateRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    progress_service.authorize_author(session, user=actor, org_id=payload.org_id)
    topic = graph_service.get_topic(session, payload.topic_id)
    if topic.org_id is not None and topic.org_id != payload.org_id:
        raise AuthorizationError("Question topic belongs to a different organization")

    if payload.objective_id:
        objective = session.get(LearningObjective, payload.objective_id)
        if objective is None:
            raise NotFoundError("Learning objective not found")
    else:
        objective = LearningObjective(
            topic_id=topic.id, statement=payload.prompt, created_by=actor.id
        )
        session.add(objective)
        session.flush()

    question = Question(
        topic_id=topic.id,
        objective_id=objective.id,
        prompt=payload.prompt,
        question_type=payload.question_type,
        reference_answer=payload.reference_answer,
        checks=payload.checks,
        created_by=actor.id,
    )
    session.add(question)
    session.flush()
    record_audit(
        session,
        action="learning.question.create",
        actor_id=actor.id,
        org_id=payload.org_id,
        resource_type="question",
        resource_id=question.id,
    )
    session.commit()
    return question


@router.get("/questions/{question_id}", response_model=QuestionResponse)
def get_question(
    question_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    question = session.get(Question, question_id)
    if question is None:
        raise NotFoundError("Question not found")
    return question


@router.post("/questions/{question_id}/rubrics", response_model=RubricResponse, status_code=201)
def create_rubric(
    question_id: str,
    payload: RubricCreateRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    progress_service.authorize_author(session, user=actor, org_id=payload.org_id)
    question = grading_service.get_question(session, question_id)
    version = 1 + session.query(Rubric).where(Rubric.question_id == question.id).count()
    rubric = Rubric(
        question_id=question.id,
        version=version,
        criteria=[criterion.model_dump() for criterion in payload.criteria],
        created_by=actor.id,
    )
    session.add(rubric)
    session.flush()
    record_audit(
        session,
        action="learning.rubric.create",
        actor_id=actor.id,
        org_id=payload.org_id,
        resource_type="rubric",
        resource_id=rubric.id,
        details={"version": version},
    )
    session.commit()
    return rubric


@router.get("/questions/{question_id}/rubrics", response_model=list[RubricResponse])
def list_rubrics(
    question_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return list(
        session.query(Rubric)
        .where(Rubric.question_id == question_id)
        .order_by(Rubric.version.desc())
        .all()
    )


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------
@router.post("/assessments", response_model=AttemptResponse, status_code=201)
def create_attempt(
    payload: AttemptCreateRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    grading_service.get_question(session, payload.question_id)
    attempt = grading_service.create_attempt(
        session,
        question_id=payload.question_id,
        learner_id=actor.id,
        org_id=payload.org_id,
        idempotency_key=payload.idempotency_key,
    )
    session.commit()
    return _attempt_response(session, attempt)


@router.get("/assessments/{attempt_id}", response_model=AttemptResponse)
def get_attempt(
    attempt_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    attempt = session.get(AssessmentAttempt, attempt_id)
    if attempt is None or attempt.learner_id != actor.id:
        raise NotFoundError("Attempt not found")
    return _attempt_response(session, attempt)


@router.post("/assessments/{attempt_id}/submit", response_model=AttemptResponse)
def submit_attempt(
    attempt_id: str,
    payload: AttemptSubmitRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    attempt = session.get(AssessmentAttempt, attempt_id)
    if attempt is None or attempt.learner_id != actor.id:
        raise NotFoundError("Attempt not found")
    grading_service.submit_attempt(
        session, attempt=attempt, response_text=payload.response_text
    )
    progress_service.record_progress(session, attempt)
    record_audit(
        session,
        action="learning.attempt.submit",
        actor_id=actor.id,
        org_id=attempt.org_id,
        resource_type="assessment_attempt",
        resource_id=attempt.id,
        details={"score": attempt.score, "max_score": attempt.max_score},
    )
    session.commit()
    return _attempt_response(session, attempt)


def _attempt_response(session: Session, attempt: AssessmentAttempt) -> AttemptResponse:
    results = list(
        session.query(CriterionResult)
        .where(CriterionResult.attempt_id == attempt.id)
        .order_by(CriterionResult.criterion_id.asc())
        .all()
    )
    return AttemptResponse(
        id=attempt.id,
        question_id=attempt.question_id,
        status=attempt.status,
        score=attempt.score,
        max_score=attempt.max_score,
        rubric_version=attempt.rubric_version,
        evaluator_version=attempt.evaluator_version,
        submitted_at=attempt.submitted_at,
        criterion_results=[
            CriterionResultResponse.model_validate(result) for result in results
        ],
    )


# ---------------------------------------------------------------------------
# LearnTrace
# ---------------------------------------------------------------------------
@router.get("/learners/me/mastery", response_model=list[MasteryResponse])
def my_mastery(
    org_id: str | None = None,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return mastery_service.list_mastery(
        session, learner_id=actor.id, org_id=org_id
    )


@router.post("/learners/me/diagnoses", response_model=DiagnosisResponse)
def create_diagnosis(
    payload: DiagnosisCreateRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    diagnosis = diagnosis_service.diagnose(
        session,
        learner_id=actor.id,
        concept_id=payload.concept_id,
        org_id=payload.org_id,
    )
    record_audit(
        session,
        action="learning.diagnosis.create",
        actor_id=actor.id,
        org_id=payload.org_id,
        resource_type="diagnosis",
        resource_id=diagnosis.id,
        details={"status": diagnosis.status},
    )
    session.commit()
    return diagnosis


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
@router.get("/learners/me/reviews/due", response_model=list[ReviewResponse])
def due_reviews(
    org_id: str | None = None,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    schedules = retention_service.due_reviews(
        session, learner_id=actor.id, org_id=org_id, limit=50
    )
    return [
        ReviewResponse(
            schedule_id=schedule.id,
            status=schedule.status,
            due_at=schedule.due_at,
            interval_days=schedule.interval_days,
            repetitions=schedule.repetitions,
            lapses=schedule.lapses,
        )
        for schedule in schedules
    ]


@router.post("/learners/me/reviews/{schedule_id}", response_model=ReviewResponse)
def submit_review(
    schedule_id: str,
    payload: ReviewSubmitRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    schedule = session.get(retention_service.RetentionSchedule, schedule_id)
    if schedule is None or schedule.learner_id != actor.id:
        raise NotFoundError("Review schedule not found")
    updated = retention_service.record_review(
        session,
        schedule=schedule,
        recalled=payload.recalled,
        quality=payload.quality,
        duration_ms=payload.duration_ms,
    )
    record_audit(
        session,
        action="retention.review.submit",
        actor_id=actor.id,
        org_id=updated.org_id,
        resource_type="retention_schedule",
        resource_id=updated.id,
        details={"recalled": payload.recalled, "quality": payload.quality},
    )
    session.commit()
    return ReviewResponse(
        schedule_id=updated.id,
        status=updated.status,
        due_at=updated.due_at,
        interval_days=updated.interval_days,
        repetitions=updated.repetitions,
        lapses=updated.lapses,
    )
