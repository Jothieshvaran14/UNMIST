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
from ..logging import get_logger
from ..models.job import Job, JobStatus

JobHandler = Callable[[Session, Job], None]

_HANDLERS: dict[str, JobHandler] = {}


class PermanentJobError(Exception):
    """A failure that will never succeed on retry (bad input, gone resource).

    The queue marks such jobs ``dead`` immediately instead of burning the
    retry budget, so malformed input cannot spin a worker forever.
    """


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
    """Claim the next runnable job, marking it running and counting the attempt.

    Claiming is the single place ``attempts`` is incremented, so a job that
    keeps failing exhausts ``max_attempts`` and dies instead of retrying
    forever. The claimed job also records ``started_at`` as its lease start.
    """
    now = now or datetime.now(timezone.utc)
    query = select(Job).where(
        Job.status == JobStatus.pending.value,
        Job.available_at <= now,
    )
    if job_type:
        query = query.where(Job.job_type == job_type)
    query = query.order_by(Job.created_at.asc()).limit(1).with_for_update()
    job = session.execute(query).scalar_one_or_none()
    if job is None:
        return None
    job.status = JobStatus.running.value
    job.attempts += 1
    job.started_at = now
    session.flush()
    return job


def reclaim_expired(
    session: Session, *, lease_seconds: int, now: Optional[datetime] = None
) -> list[str]:
    """Return abandoned running jobs to the queue.

    A job stuck in ``running`` past its lease (worker crash, lost process) is
    reset to ``pending`` so it can be retried. If it has exhausted its attempts
    it is marked ``dead`` rather than looping. Returns affected job ids.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=lease_seconds)
    rows = (
        session.execute(
            select(Job).where(
                Job.status == JobStatus.running.value,
                Job.started_at <= cutoff,
            )
        )
        .scalars()
        .all()
    )
    reclaimed: list[str] = []
    for job in rows:
        if job.attempts >= job.max_attempts:
            job.status = JobStatus.dead.value
            job.finished_at = now
            job.error = (job.error or "") + " | lease expired; attempts exhausted"
        else:
            job.status = JobStatus.pending.value
            job.available_at = now
        reclaimed.append(job.id)
    session.flush()
    return reclaimed


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


def claim_still_held(
    session: Session, job_id: str, claimed_started_at: Optional[datetime]
) -> bool:
    """True if this worker still owns the lease it took for ``job_id``.

    A job can be reclaimed (lease expired) and re-claimed by another worker
    while this one is still executing. The row is re-read rather than trusted
    from the identity map, because sessions may run with
    ``expire_on_commit=False`` and would otherwise report our own stale copy —
    ``populate_existing`` forces the reload.

    ``started_at`` is compared as well as ``status`` so a job that was
    reclaimed *and* handed straight to a second worker (both copies read
    ``running``) is still correctly recognised as no longer ours.
    """
    fresh = session.execute(
        select(Job).where(Job.id == job_id).execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if fresh is None or fresh.status != JobStatus.running.value:
        return False
    if claimed_started_at is None or fresh.started_at is None:
        return True
    # Second-level granularity matches DocumentPipeline._still_owns_lease.
    return fresh.started_at.replace(microsecond=0) == claimed_started_at.replace(
        microsecond=0
    )


def run_once(
    session_factory: sessionmaker[Session],
    *,
    job_type: Optional[str] = None,
    lease_seconds: Optional[int] = None,
) -> Optional[Job]:
    """Claim and execute a single job. Returns the job or None if the queue is empty."""
    if lease_seconds is None:
        lease_seconds = get_settings().job_lease_seconds
    session = session_factory()
    try:
        # Reclaim jobs abandoned by a crashed worker before claiming new work.
        reclaim_expired(session, lease_seconds=lease_seconds)
        session.commit()
        job = claim_next(session, job_type=job_type)
        if job is None:
            return None
        handler = _HANDLERS.get(job.job_type)
        if handler is None:
            mark_failed(session, job, f"No handler registered for job type '{job.job_type}'")
            session.commit()
            return job
        claimed_started_at = job.started_at
        try:
            handler(session, job)
            if claim_still_held(session, job.id, claimed_started_at):
                mark_succeeded(session, job)
            else:
                # The lease was reclaimed while the handler ran, so another
                # worker owns this job now. The document pipelines return a
                # short outcome string (rather than raising) when they detect a
                # lost lease, and handlers do not forward it — so without this
                # check a deliberately-abandoned run would still be recorded
                # ``succeeded``. That would suppress the retry the reclaim
                # machinery is about to schedule and leave the document stuck in
                # an in-flight state. Leave the row to the reclaim machinery.
                get_logger("unmist.jobs").warning(
                    "lease lost during execution; not marking job succeeded",
                    extra={"job_id": job.id, "job_type": job.job_type},
                )
                return job
        except PermanentJobError as exc:
            # Input will never succeed on retry; do not burn the retry budget.
            session.rollback()
            job = session.get(Job, job.id)
            if job is not None:
                job.error = str(exc)[:4000]
                job.status = JobStatus.dead.value
                job.finished_at = datetime.now(timezone.utc)
                session.flush()
            session.commit()
            return job
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
