"""LearnTrace prerequisite-gap diagnosis tests."""

from __future__ import annotations

from _db_helpers import make_user
from app.models.knowledge import Concept, LearningObjective
from app.models.learning import Question
from app.services.learning import diagnosis


def _graph_concepts(db_session):
    """Create a user, a topic with two linked concepts, and a published graph version."""
    from app.models.knowledge import (
        Concept,
        ConceptRelationship,
        GraphBuild,
        GraphVersion,
        Topic,
    )

    user = make_user(db_session, email="diagnosis@example.com")
    topic = Topic(name="Functions", slug="functions")
    db_session.add(topic)
    db_session.flush()
    prerequisite = Concept(
        topic_id=topic.id, stable_key="closures", canonical_name="Closures"
    )
    target = Concept(
        topic_id=topic.id, stable_key="decorators", canonical_name="Decorators"
    )
    db_session.add_all([prerequisite, target])
    db_session.flush()
    db_session.add(
        ConceptRelationship(
            from_concept_id=prerequisite.id,
            to_concept_id=target.id,
            relationship_type="prerequisite_of",
        )
    )
    build = GraphBuild(topic_id=topic.id, status="succeeded", trigger="manual")
    db_session.add(build)
    db_session.flush()
    snapshot = {
        "concepts": [
            {"stable_key": "closures", "canonical_name": "Closures"},
            {"stable_key": "decorators", "canonical_name": "Decorators"},
        ],
        "relationships": [
            {
                "from_key": "closures",
                "to_key": "decorators",
                "relationship_type": "prerequisite_of",
            }
        ],
    }
    db_session.add(
        GraphVersion(
            build_id=build.id,
            topic_id=topic.id,
            version=1,
            status="published",
            snapshot=snapshot,
            validation_report={},
            concept_count=2,
            relationship_count=1,
        )
    )
    db_session.flush()
    return user, topic, target, prerequisite


def test_diagnosis_without_graph_is_insufficient(db_session):
    user = make_user(db_session, email="diag-none@example.com")
    topic_name = "No Graph Topic"
    topic = __import__("app.models.knowledge", fromlist=["Topic"]).Topic(
        name=topic_name, slug="no-graph"
    )
    db_session.add(topic)
    db_session.flush()
    concept = Concept(topic_id=topic.id, stable_key="target", canonical_name="Target")
    db_session.add(concept)
    db_session.flush()

    result = diagnosis.diagnose(
        db_session, learner_id=user.id, concept_id=concept.id, org_id=None
    )
    assert result.status == "insufficient_evidence"
    assert result.hypotheses == []


def test_diagnosis_with_graph_but_no_question_is_insufficient(db_session):
    user, _topic, target, _prereq = _graph_concepts(db_session)

    result = diagnosis.diagnose(
        db_session, learner_id=user.id, concept_id=target.id, org_id=None
    )
    assert result.status == "insufficient_evidence"
    assert result.hypotheses  # prerequisite hypotheses were generated


def test_diagnosis_selects_target_when_question_available(db_session):
    user, topic, target, prerequisite = _graph_concepts(db_session)

    objective = LearningObjective(
        topic_id=topic.id, concept_id=prerequisite.id, statement="Explain closures"
    )
    db_session.add(objective)
    db_session.flush()
    question = Question(
        topic_id=topic.id,
        objective_id=objective.id,
        prompt="What is a closure?",
        question_type="short_answer",
    )
    db_session.add(question)
    db_session.flush()

    result = diagnosis.diagnose(
        db_session, learner_id=user.id, concept_id=target.id, org_id=None
    )
    assert result.status == "awaiting_response"
    assert result.selected_question_id == question.id
    assert result.conclusion
    assert result.evidence_summary


def test_diagnosis_does_not_invent_missing_prerequisites(db_session):
    user, _topic, target, _prereq = _graph_concepts(db_session)
    keys = diagnosis.prerequisite_keys(db_session, target)
    assert keys == ["closures"]
    assert user.id  # user created for FK compliance
