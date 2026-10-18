"""Standalone worker entrypoint.

Usage::

    python -m app.workers.runner            # run forever
    python -m app.workers.runner --once     # process a single job
    python -m app.workers.runner --list     # list registered job types
"""

from __future__ import annotations

import argparse

from ..config import load_settings
from ..db import get_session_factory
from ..logging import configure_logging, get_logger
from ..services import jobs
from . import handlers  # noqa: F401 - ensure handlers are registered


def main() -> None:
    parser = argparse.ArgumentParser(description="Unmist background worker")
    parser.add_argument("--once", action="store_true", help="Process a single job and exit")
    parser.add_argument("--list", action="store_true", help="List registered job types")
    parser.add_argument("--type", default=None, help="Only process this job type")
    args = parser.parse_args()

    settings = load_settings()
    configure_logging(settings.log_level)
    logger = get_logger("unmist.worker")

    if args.list:
        for job_type in jobs.known_job_types():
            print(job_type)
        return

    factory = get_session_factory()
    if args.once:
        job = jobs.run_once(factory, job_type=args.type)
        if job is None:
            logger.info("No pending jobs")
        else:
            logger.info("Processed job", extra={"job_id": job.id, "status": job.status})
        return

    logger.info("Worker starting", extra={"types": jobs.known_job_types()})
    jobs.run_forever(factory, poll_interval=settings.job_poll_interval_seconds)


if __name__ == "__main__":
    main()
