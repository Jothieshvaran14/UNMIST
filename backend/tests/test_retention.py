"""Retention scheduling tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.knowledge import Concept, Topic
from app.services.retention import scheduling


def _setup(session):
    from _db_helpers import make_user

    user = make_user(session, email="retention-learner@example.com")
    topic = Topic(name="T", slug="retention-topic")
    session.add(topic)
    session.flush()
    concept = Concept(topic_id=topic.id, stable_key="c", canonical_name="C")
    session.add(concept)
    session.flush()
    return user, concept


def test_schedule_creation_is_idempotent(db_session):
    user, concept = _setup(db_session)
    first = scheduling.get_or_create_schedule(
        db_session, learner_id=user.id, concept_id=concept.id, org_id=None
    )
    second = scheduling.get_or_create_schedule(
        db_session, learner_id=user.id, concept_id=concept.id, org_id=None
    )
    assert first.id == second.id


def test_record_review_updates_schedule(db_session):
    user, concept = _setup(db_session)
    schedule = scheduling.get_or_create_schedule(
        db_session, learner_id=user.id, concept_id=concept.id, org_id=None
    )
    updated = scheduling.record_review(
        db_session, schedule=schedule, recalled=True, quality=5
    )
    assert updated.repetitions == 1
    assert updated.interval_days == 1
    assert updated.due_at > datetime.now(timezone.utc)
    assert updated.status == "demonstrated"


def test_failed_recall_resets_repetition(db_session):
    user, concept = _setup(db_session)
    schedule = scheduling.get_or_create_schedule(
        db_session, learner_id=user.id, concept_id=concept.id, org_id=None
    )
    schedule.repetitions = 3
    schedule.interval_days = 10
    db_session.flush()
    updated = scheduling.record_review(
        db_session, schedule=schedule, recalled=False, quality=1
    )
    assert updated.repetitions == 0
    assert updated.lapses == 1
    assert updated.status == "learning"


def test_due_reviews_only_returns_due(db_session):
    user, concept = _setup(db_session)
    from app.models.knowledge import Concept, Topic

    # second concept for the "not due" case
    topic2 = Topic(name="T2", slug="retention-topic-2")
    db_session.add(topic2)
    db_session.flush()
    concept2 = Concept(topic_id=topic2.id, stable_key="c2", canonical_name="C2")
    db_session.add(concept2)
    db_session.flush()

    due = scheduling.get_or_create_schedule(
        db_session, learner_id=user.id, concept_id=concept.id, org_id=None
    )
    due.due_at = datetime.now(timezone.utc) - timedelta(hours=1)
    future = scheduling.get_or_create_schedule(
        db_session, learner_id=user.id, concept_id=concept2.id, org_id=None
    )
    future.due_at = datetime.now(timezone.utc) + timedelta(days=5)
    db_session.flush()

    results = scheduling.due_reviews(db_session, learner_id=user.id, org_id=None)
    assert [s.id for s in results] == [due.id]


def test_get_or_create_returns_same_schedule(db_session):
    user, concept = _setup(db_session)
    first = scheduling.get_or_create_schedule(
        db_session, learner_id=user.id, concept_id=concept.id, org_id=None
    )
    second = scheduling.get_or_create_schedule(
        db_session, learner_id=user.id, concept_id=concept.id, org_id=None
    )
    assert first.id == second.id
