"""LearnTrace mastery estimation.

A Bayesian Knowledge Tracing (BKT) baseline is used to represent mastery
probabilistically rather than as a fixed label. Parameters are *provisional*
until calibrated against a reviewed dataset (see docs/ai/model-evaluation.md).

The model records the version, evidence count, and timestamp so results remain
traceable and comparable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.learning import MasteryEstimate, MasteryState

MODEL_VERSION = "bkt-provisional-v1"

# Provisional parameters. These must be calibrated before being treated as
# validated; they are intentionally documented as estimates.
P_INIT = 0.3
P_LEARN = 0.12
P_GUESS = 0.2
P_SLIP = 0.1
P_FORGET = 0.05


def _clamp(value: float) -> float:
    return max(0.01, min(0.99, value))


def bkt_update(
    p_know: float,
    correct: bool,
    *,
    difficulty: float = 1.0,
    guess: float = P_GUESS,
    slip: float = P_SLIP,
    learn: float = P_LEARN,
    forget: float = P_FORGET,
) -> float:
    """Apply one BKT observation.

    Difficulty scales guess/slip/forget so that harder items provide weaker
    evidence per observation.
    """
    guess = _clamp(guess * (0.7 + 0.6 * difficulty))
    slip = _clamp(slip * (0.7 + 0.6 * difficulty))
    learn = _clamp(learn / max(0.5, difficulty))
    forget = _clamp(forget * difficulty)

    if correct:
        numerator = p_know * (1 - slip)
        denominator = numerator + (1 - p_know) * guess
        posterior = numerator / denominator if denominator else p_know
        updated = posterior + (1 - posterior) * learn
    else:
        numerator = p_know * slip
        denominator = numerator + (1 - p_know) * (1 - guess)
        posterior = numerator / denominator if denominator else p_know
        updated = posterior * (1 - forget)
    return _clamp(updated)


def classify(mastery_probability: float, evidence_count: int) -> str:
    if evidence_count <= 0:
        return MasteryState.never_assessed.value
    if evidence_count < 2:
        return MasteryState.insufficient_evidence.value
    if mastery_probability >= 0.85:
        return MasteryState.demonstrated.value
    if mastery_probability >= 0.6:
        return MasteryState.learning.value
    return MasteryState.failing.value


def get_mastery(
    session: Session,
    *,
    learner_id: str,
    concept_id: str,
    org_id: Optional[str],
) -> MasteryEstimate:
    estimate = session.execute(
        select(MasteryEstimate).where(
            MasteryEstimate.learner_id == learner_id,
            MasteryEstimate.concept_id == concept_id,
            MasteryEstimate.org_id == org_id,
        )
    ).scalar_one_or_none()
    if estimate is None:
        estimate = MasteryEstimate(
            learner_id=learner_id,
            concept_id=concept_id,
            org_id=org_id,
            mastery_probability=P_INIT,
            model_version=MODEL_VERSION,
            evidence_count=0,
            state=MasteryState.never_assessed.value,
        )
        session.add(estimate)
        session.flush()
    return estimate


def update_mastery(
    session: Session,
    *,
    learner_id: str,
    concept_id: str,
    org_id: Optional[str],
    correct: bool,
    difficulty: float = 1.0,
) -> MasteryEstimate:
    estimate = get_mastery(
        session, learner_id=learner_id, concept_id=concept_id, org_id=org_id
    )
    new_probability = bkt_update(
        estimate.mastery_probability, correct, difficulty=difficulty
    )
    estimate.mastery_probability = round(new_probability, 4)
    estimate.evidence_count += 1
    estimate.state = classify(new_probability, estimate.evidence_count)
    estimate.model_version = MODEL_VERSION
    estimate.last_activity_at = datetime.now(timezone.utc)
    session.flush()
    return estimate


def list_mastery(
    session: Session, *, learner_id: str, org_id: Optional[str]
) -> list[MasteryEstimate]:
    return list(
        session.execute(
            select(MasteryEstimate).where(
                MasteryEstimate.learner_id == learner_id,
                MasteryEstimate.org_id == org_id,
            )
        ).scalars().all()
    )
