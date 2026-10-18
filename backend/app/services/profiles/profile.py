"""Private knowledge profile.

The private profile is the learner's own structured view. It distinguishes what
the learner claims from what has been demonstrated, and separates initial
mastery from retention.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.knowledge import Concept, Topic
from ...models.learning import AssessmentAttempt, Diagnosis, MasteryEstimate
from ...models.retention import RetentionSchedule


def build_profile(session: Session, *, learner_id: str, org_id: Optional[str]) -> dict:
    mastery_rows = list(
        session.execute(
            select(MasteryEstimate, Concept)
            .join(Concept, Concept.id == MasteryEstimate.concept_id)
            .where(
                MasteryEstimate.learner_id == learner_id,
                MasteryEstimate.org_id == org_id,
            )
        ).all()
    )
    mastery = []
    gaps = []
    for estimate, concept in mastery_rows:
        entry = {
            "concept_id": concept.id,
            "canonical_name": concept.canonical_name,
            "topic_id": concept.topic_id,
            "mastery_probability": estimate.mastery_probability,
            "evidence_count": estimate.evidence_count,
            "state": estimate.state,
            "model_version": estimate.model_version,
            "last_activity_at": estimate.last_activity_at,
        }
        mastery.append(entry)
        if estimate.mastery_probability < 0.6:
            gaps.append(entry)

    schedules = list(
        session.execute(
            select(RetentionSchedule, Concept)
            .join(Concept, Concept.id == RetentionSchedule.concept_id)
            .where(
                RetentionSchedule.learner_id == learner_id,
                RetentionSchedule.org_id == org_id,
            )
        ).all()
    )
    retention = [
        {
            "concept_id": concept.id,
            "canonical_name": concept.canonical_name,
            "status": schedule.status,
            "due_at": schedule.due_at,
            "interval_days": schedule.interval_days,
            "lapses": schedule.lapses,
            "timezone": schedule.timezone,
        }
        for schedule, concept in schedules
    ]

    attempts = list(
        session.execute(
            select(AssessmentAttempt)
            .where(
                AssessmentAttempt.learner_id == learner_id,
                AssessmentAttempt.org_id == org_id,
            )
            .order_by(AssessmentAttempt.created_at.desc())
            .limit(100)
        ).scalars().all()
    )

    diagnoses = list(
        session.execute(
            select(Diagnosis)
            .where(Diagnosis.learner_id == learner_id, Diagnosis.org_id == org_id)
            .order_by(Diagnosis.created_at.desc())
            .limit(50)
        ).scalars().all()
    )

    topics = list(
        session.execute(
            select(Topic).where(Topic.org_id == org_id).order_by(Topic.created_at.desc())
        ).scalars().all()
    )

    return {
        "learner_id": learner_id,
        "org_id": org_id,
        "mastery": mastery,
        "retention": retention,
        "prerequisite_gaps": gaps,
        "assessment_history": [
            {
                "attempt_id": attempt.id,
                "question_id": attempt.question_id,
                "status": attempt.status,
                "score": attempt.score,
                "max_score": attempt.max_score,
                "rubric_version": attempt.rubric_version,
                "evaluator_version": attempt.evaluator_version,
                "submitted_at": attempt.submitted_at,
            }
            for attempt in attempts
        ],
        "diagnoses": [
            {
                "diagnosis_id": diagnosis.id,
                "concept_id": diagnosis.concept_id,
                "status": diagnosis.status,
                "conclusion": diagnosis.conclusion,
                "created_at": diagnosis.created_at,
            }
            for diagnosis in diagnoses
        ],
        "topics": [
            {"topic_id": topic.id, "name": topic.name, "slug": topic.slug}
            for topic in topics
        ],
    }
