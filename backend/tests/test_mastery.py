"""LearnTrace mastery model tests."""

from __future__ import annotations

from app.models.knowledge import Concept, Topic
from app.services.learning import mastery


def test_bkt_correct_increases_mastery():
    before = 0.3
    after = mastery.bkt_update(before, correct=True)
    assert after > before


def test_bkt_incorrect_decreases_mastery():
    before = 0.8
    after = mastery.bkt_update(before, correct=False)
    assert after < before


def test_bkt_is_bounded():
    value = 0.5
    for _ in range(20):
        value = mastery.bkt_update(value, correct=True)
    assert 0 < value < 1


def test_classify_states():
    assert mastery.classify(0.5, 0) == "never_assessed"
    assert mastery.classify(0.5, 1) == "insufficient_evidence"
    assert mastery.classify(0.7, 3) == "learning"
    assert mastery.classify(0.9, 3) == "demonstrated"
    assert mastery.classify(0.2, 3) == "failing"


def test_update_mastery_persists(db_session):
    from _db_helpers import make_user

    user = make_user(db_session, email="mastery@example.com")
    topic = Topic(name="T", slug="t")
    db_session.add(topic)
    db_session.flush()
    concept = Concept(topic_id=topic.id, stable_key="c", canonical_name="C")
    db_session.add(concept)
    db_session.flush()

    first = mastery.update_mastery(
        db_session, learner_id=user.id, concept_id=concept.id, org_id=None, correct=True
    )
    assert first.evidence_count == 1
    assert first.state == "insufficient_evidence"  # only one observation
    first_probability = first.mastery_probability

    second = mastery.update_mastery(
        db_session, learner_id=user.id, concept_id=concept.id, org_id=None, correct=True
    )
    assert second.evidence_count == 2
    assert second.mastery_probability > first_probability
    assert second.state in {"learning", "demonstrated"}
    assert second.model_version == mastery.MODEL_VERSION
