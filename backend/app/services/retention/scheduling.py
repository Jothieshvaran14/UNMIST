"""Retention scheduling (spaced repetition).

Scheduling is timezone-aware and idempotent. The due-review queue is a durable
projection of per-learner schedules, so restarts never lose pending reviews.

Mastery and retention are tracked separately: a concept can be demonstrated
(mastery) and later at risk of being forgotten (retention). This module never
conflates the two.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as tz
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.retention import RetentionSchedule, RetentionStatus, ReviewAttempt

DEFAULT_EASE = 2.5
MIN_EASE = 1.3


def get_or_create_schedule(
    session: Session,
    *,
    learner_id: str,
    concept_id: str,
    org_id: Optional[str],
    learner_timezone: str = "UTC",
) -> RetentionSchedule:
    schedule = session.execute(
        select(RetentionSchedule).where(
            RetentionSchedule.learner_id == learner_id,
            RetentionSchedule.concept_id == concept_id,
            RetentionSchedule.org_id == org_id,
        )
    ).scalar_one_or_none()
    if schedule is None:
        schedule = RetentionSchedule(
            learner_id=learner_id,
            concept_id=concept_id,
            org_id=org_id,
            status=RetentionStatus.never_assessed.value,
            due_at=datetime.now(tz.utc),
            timezone=learner_timezone,
        )
        session.add(schedule)
        session.flush()
    return schedule


def schedule_for_concept(
    session: Session,
    *,
    learner_id: str,
    concept_id: str,
    org_id: Optional[str],
    learner_timezone: str = "UTC",
) -> RetentionSchedule:
    return get_or_create_schedule(
        session,
        learner_id=learner_id,
        concept_id=concept_id,
        org_id=org_id,
        learner_timezone=learner_timezone,
    )


def _next_interval(schedule: RetentionSchedule, quality: int) -> tuple[int, float]:
    if quality < 3:
        return 1, max(MIN_EASE, schedule.ease_factor - 0.2)
    repetitions = schedule.repetitions + 1
    if repetitions == 1:
        interval = 1
    elif repetitions == 2:
        interval = 6
    else:
        interval = max(1, round(schedule.interval_days * schedule.ease_factor))
    ease_delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    ease = max(MIN_EASE, schedule.ease_factor + ease_delta)
    return interval, ease


def record_review(
    session: Session,
    *,
    schedule: RetentionSchedule,
    recalled: bool,
    quality: int,
    duration_ms: Optional[int] = None,
) -> RetentionSchedule:
    quality = max(0, min(5, int(quality)))
    interval, ease = _next_interval(schedule, quality)
    now = datetime.now(tz.utc)

    schedule.repetitions = 0 if quality < 3 else schedule.repetitions + 1
    schedule.lapses = schedule.lapses + (1 if not recalled else 0)
    schedule.ease_factor = round(ease, 3)
    schedule.interval_days = interval
    schedule.due_at = now + timedelta(days=interval)
    schedule.last_review_at = now
    schedule.timezone = schedule.timezone or "UTC"

    if schedule.repetitions == 0:
        schedule.status = RetentionStatus.learning.value
    elif schedule.lapses > 0:
        schedule.status = RetentionStatus.at_risk.value
    elif schedule.interval_days >= 21:
        schedule.status = RetentionStatus.retained.value
    else:
        schedule.status = RetentionStatus.demonstrated.value

    session.add(
        ReviewAttempt(
            schedule_id=schedule.id,
            learner_id=schedule.learner_id,
            concept_id=schedule.concept_id,
            org_id=schedule.org_id,
            recalled=recalled,
            quality=quality,
            duration_ms=duration_ms,
            prompt_type="recall",
        )
    )
    session.flush()
    return schedule


def due_reviews(
    session: Session,
    *,
    learner_id: str,
    org_id: Optional[str],
    now: Optional[datetime] = None,
    limit: int = 50,
) -> list[RetentionSchedule]:
    now = now or datetime.now(tz.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz.utc)
    return list(
        session.execute(
            select(RetentionSchedule)
            .where(
                RetentionSchedule.learner_id == learner_id,
                RetentionSchedule.org_id == org_id,
                RetentionSchedule.due_at <= now,
            )
            .order_by(RetentionSchedule.due_at.asc())
            .limit(limit)
        ).scalars().all()
    )


def record_review_by_id(
    session: Session,
    *,
    schedule_id: str,
    recalled: bool,
    quality: int,
    duration_ms: Optional[int] = None,
) -> RetentionSchedule:
    schedule = session.get(RetentionSchedule, schedule_id)
    if schedule is None:
        from ...errors import NotFoundError

        raise NotFoundError("Retention schedule not found")
    return record_review(
        session,
        schedule=schedule,
        recalled=recalled,
        quality=quality,
        duration_ms=duration_ms,
    )
