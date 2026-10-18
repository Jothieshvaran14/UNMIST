"""LearnTrace prerequisite-gap diagnosis.

The diagnosis generates *competing hypotheses* for why a learner may be
struggling with a target concept, selects a diagnostic question using an
information-gain heuristic, and stores the reasoning. It never treats an
incorrect answer as proof of missing knowledge, and it returns
``insufficient_evidence`` when it cannot draw a supported conclusion.

Model-generated hypotheses are kept separate from verified graph relationships.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import NotFoundError
from ...models.knowledge import Concept, GraphVersion
from ...models.learning import AssessmentAttempt, Diagnosis, Question
from . import mastery as mastery_service


def _latest_graph_version(session: Session, topic_id: str) -> Optional[GraphVersion]:
    return session.execute(
        select(GraphVersion)
        .where(GraphVersion.topic_id == topic_id)
        .order_by(GraphVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def prerequisite_keys(session: Session, concept: Concept) -> list[str]:
    """Return prerequisite concept keys for a concept from the latest graph."""
    graph = _latest_graph_version(session, concept.topic_id)
    if graph is None:
        return []
    target_key = concept.stable_key
    keys: list[str] = []
    for relationship in graph.snapshot.get("relationships", []):
        if (
            relationship.get("relationship_type") == "prerequisite_of"
            and relationship.get("to_key") == target_key
        ):
            keys.append(relationship["from_key"])
    return keys


def _expected_information_gain(mastery_probability: float) -> float:
    """Bernoulli variance peaks at p=0.5; use it as an uncertainty proxy."""
    p = max(0.01, min(0.99, mastery_probability))
    return round(p * (1 - p), 4)


def build_hypotheses(
    session: Session, *, learner_id: str, concept: Concept, org_id: Optional[str]
) -> list[dict]:
    keys = prerequisite_keys(session, concept)
    hypotheses: list[dict] = []
    for key in keys:
        prerequisite = session.execute(
            select(Concept).where(
                Concept.topic_id == concept.topic_id, Concept.stable_key == key
            )
        ).scalar_one_or_none()
        if prerequisite is None:
            continue
        estimate = mastery_service.get_mastery(
            session, learner_id=learner_id, concept_id=prerequisite.id, org_id=org_id
        )
        probability = estimate.mastery_probability
        hypotheses.append(
            {
                "concept_id": prerequisite.id,
                "stable_key": prerequisite.stable_key,
                "canonical_name": prerequisite.canonical_name,
                "mastery_probability": probability,
                "evidence_count": estimate.evidence_count,
                "likely_gap": probability < 0.6,
                "information_gain": _expected_information_gain(probability),
            }
        )
    return hypotheses


def select_diagnostic_target(hypotheses: list[dict]) -> Optional[dict]:
    """Choose the prerequisite with the most useful uncertainty to resolve."""
    if not hypotheses:
        return None
    # Prefer likely gaps, then highest information gain.
    gaps = [h for h in hypotheses if h["likely_gap"]] or hypotheses
    return max(gaps, key=lambda h: h["information_gain"])


def available_diagnostic_question(
    session: Session, *, learner_id: str, concept_id: str
) -> Optional[Question]:
    """Return an unattempted question for a concept, if any."""
    concept = session.get(Concept, concept_id)
    if concept is None:
        return None
    attempted = select(AssessmentAttempt.question_id).where(
        AssessmentAttempt.learner_id == learner_id
    )
    question = session.execute(
        select(Question)
        .where(Question.topic_id == concept.topic_id)
        .where(Question.id.not_in(attempted))
        .order_by(Question.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    return question


def diagnose(
    session: Session,
    *,
    learner_id: str,
    concept_id: str,
    org_id: Optional[str],
) -> Diagnosis:
    concept = session.get(Concept, concept_id)
    if concept is None:
        raise NotFoundError("Concept not found")

    hypotheses = build_hypotheses(
        session, learner_id=learner_id, concept=concept, org_id=org_id
    )
    target = select_diagnostic_target(hypotheses)

    if target is None:
        diagnosis = Diagnosis(
            learner_id=learner_id,
            concept_id=concept_id,
            org_id=org_id,
            hypotheses=hypotheses,
            status="insufficient_evidence",
            conclusion=(
                "No supported prerequisite relationships were found for this concept, "
                "so no gap diagnosis can be made."
            ),
            evidence_summary="No prerequisite relationships in the published graph.",
        )
        session.add(diagnosis)
        session.flush()
        return diagnosis

    question = available_diagnostic_question(
        session, learner_id=learner_id, concept_id=target["concept_id"]
    )
    if question is None:
        diagnosis = Diagnosis(
            learner_id=learner_id,
            concept_id=concept_id,
            org_id=org_id,
            hypotheses=hypotheses,
            status="insufficient_evidence",
            conclusion=(
                "Candidate prerequisite gaps were identified, but no diagnostic "
                "question is available to distinguish between them."
            ),
            evidence_summary=(
                "Hypotheses: "
                + ", ".join(h["canonical_name"] for h in hypotheses)
            ),
        )
        session.add(diagnosis)
        session.flush()
        return diagnosis

    diagnosis = Diagnosis(
        learner_id=learner_id,
        concept_id=concept_id,
        org_id=org_id,
        hypotheses=hypotheses,
        selected_question_id=question.id,
        status="awaiting_response",
        conclusion=(
            f"Most likely gap: {target['canonical_name']} "
            f"(mastery {target['mastery_probability']:.2f}, "
            f"evidence {target['evidence_count']})."
        ),
        evidence_summary=(
            "Selected by expected information gain among candidate prerequisite gaps."
        ),
    )
    session.add(diagnosis)
    session.flush()
    return diagnosis
