"""Durable background job queue.

Jobs are persisted in the database, so accepted work survives worker restarts.
Delivery is at-least-once; handlers must be idempotent. Jobs carry an
``idempotency_key`` with a unique constraint to prevent duplicate submission.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from ..models.job import Job, JobStatus

JobHandler = Callable[[Session, Job], None]

_HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(func: JobHandler) -> JobHandler:
        _HANDLERS[job_type] = func
        return func

    return decorator


def known_job_types() -> list[str]:
    return sorted(_HANDLERS)


def enqueue(
    session: Session,
    *,
    job_type: str,
    payload: dict,
    tenant_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    available_at: Optional[datetime] = None,
) -> Job:
    """Create a job, or return the existing job for a repeated idempotency key."""
    if idempotency_key:
        existing = session.execute(
            select(Job).where(Job.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    settings = get_settings()
    job = Job(
        job_type=job_type,
        tenant_id=tenant_id,
        payload=payload,
        status=JobStatus.pending.value,
        max_attempts=settings.job_max_attempts,
        idempotency_key=idempotency_key,
        available_at=available_at or datetime.now(timezone.utc),
    )
    session.add(job)
    session.flush()
    return job


def claim_next(
    session: Session, *, job_type: Optional[str] = None, now: Optional[datetime] = None
) -> Optional[Job]:
    now = now or datetime.now(timezone.utc)
    query = select(Job).where(
        Job.status == JobStatus.pending.value,
        Job.available_at <= now,
    )
    if job_type:
        query = query.where(Job.job_type == job_type)
    query = query.order_by(Job.created_at.asc()).limit(1).with_for_update()
    return session.execute(query).scalar_one_or_none()


def mark_succeeded(session: Session, job: Job) -> None:
    job.status = JobStatus.succeeded.value
    job.finished_at = datetime.now(timezone.utc)
    job.error = None
    session.flush()


def mark_failed(
    session: Session, job: Job, error: str, *, retry_delay_seconds: float = 30.0
) -> None:
    job.error = error[:4000]
    if job.attempts >= job.max_attempts:
        job.status = JobStatus.dead.value
        job.finished_at = datetime.now(timezone.utc)
    else:
        job.status = JobStatus.pending.value
        job.available_at = datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)
    session.flush()


def run_once(
    session_factory: sessionmaker[Session], *, job_type: Optional[str] = None
) -> Optional[Job]:
    """Claim and execute a single job. Returns the job or None if the queue is empty."""
    session = session_factory()
    try:
        job = claim_next(session, job_type=job_type)
        if job is None:
            return None
        handler = _HANDLERS.get(job.job_type)
        if handler is None:
            mark_failed(session, job, f"No handler registered for job type '{job.job_type}'")
            session.commit()
            return job
        try:
            handler(session, job)
            mark_succeeded(session, job)
        except Exception as exc:  # noqa: BLE001 - persist the failure, then re-raise
            session.rollback()
            # Re-claim by id after rollback so we can record the failure.
            job = session.get(Job, job.id)
            if job is not None:
                mark_failed(session, job, f"{type(exc).__name__}: {exc}")
            session.commit()
            raise
        session.commit()
        return job
    finally:
        session.close()


def run_forever(session_factory: sessionmaker[Session], *, poll_interval: float = 1.0) -> None:
    while True:
        try:
            run_once(session_factory)
        except Exception:  # noqa: BLE001 - keep the worker alive
            from ..logging import get_logger

            get_logger("unmist.worker").exception("Job execution failed")
        time.sleep(poll_interval)
