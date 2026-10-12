"""Background jobs on Postgres."""

from pathlib import Path

from .models import CronJob, Job
from .registry import JobContext, JobRegistry, enqueue
from .schedule import next_run, parse_schedule
from .worker import Worker, worker_lifespan

__all__ = [
    "CronJob",
    "Job",
    "JobContext",
    "JobRegistry",
    "Worker",
    "enqueue",
    "migrations_path",
    "next_run",
    "parse_schedule",
    "worker_lifespan",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.jobs"])``."""
    return Path(__file__).parent / "migrations" / "versions"
