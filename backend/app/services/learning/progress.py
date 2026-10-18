"""Assessment authoring authorization and post-grading progress hooks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ...errors import AuthorizationError
from ...models.knowledge import LearningObjective
from ...models.learning import AssessmentAttempt, Question
from ...security.dependencies import get_membership
from ...security.rbac import role_at_least
from ..learning import mastery as mastery_service
from ..retention import scheduling as retention_service


def authorize_author(
    session: Session,
    *,
    user,
    org_id: Optional[str],
    minimum_role: str = "faculty",
) -> None:
    """Only faculty/admin/owner (or superusers) may author assessments and rubrics."""
    if getattr(user, "is_superuser", False):
        return
    if org_id is None:
        raise AuthorizationError("An organization context is required to author assessments")
    membership = get_membership(session, user.id, org_id)
    if membership is None:
        raise AuthorizationError("You are not a member of this organization")
    if not role_at_least(membership.role, minimum_role):
        raise AuthorizationError(
            f"Authoring assessments requires the '{minimum_role}' role or higher"
        )


def record_progress(session: Session, attempt: AssessmentAttempt) -> None:
    """Update mastery and retention after an attempt is graded.

    A passing attempt (score >= 60% of max) counts as a correct observation;
    otherwise it counts as incorrect. Retention is scheduled separately from
    mastery so that forgetting is not confused with never having learned.
    """
    question = session.get(Question, attempt.question_id)
    if question is None or not question.objective_id:
        return
    objective = session.get(LearningObjective, question.objective_id)
    if objective is None or objective.concept_id is None:
        return

    max_score = attempt.max_score or 0.0
    correct = bool(max_score) and attempt.score >= 0.6 * max_score

    mastery_service.update_mastery(
        session,
        learner_id=attempt.learner_id,
        concept_id=objective.concept_id,
        org_id=attempt.org_id,
        correct=correct,
        difficulty=1.0,
    )
    schedule = retention_service.get_or_create_schedule(
        session,
        learner_id=attempt.learner_id,
        concept_id=objective.concept_id,
        org_id=attempt.org_id,
    )
    if correct and schedule.status == "never_assessed":
        from ...models.retention import RetentionStatus

        schedule.status = RetentionStatus.demonstrated.value
        schedule.due_at = datetime.now(timezone.utc) + timedelta(days=1)
        session.flush()
