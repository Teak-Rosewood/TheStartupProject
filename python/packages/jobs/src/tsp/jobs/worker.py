from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import socket
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from tsp.db import Database

from .models import CronJob
from .registry import JobContext, JobRegistry, enqueue
from .schedule import next_run

log = logging.getLogger("tsp.jobs")


def backoff(attempt: int) -> timedelta:
    """10 s, 20 s, 40 s ... capped at one hour, with jitter so retries do not stampede."""
    return timedelta(seconds=min(3600, 10 * 2 ** (attempt - 1)) * random.uniform(0.8, 1.2))


async def claim_jobs(
    db: Database, queues: Sequence[str], worker_id: str, lease: timedelta, limit: int
) -> list[dict[str, Any]]:
    """Lease up to ``limit`` due jobs from ``queues`` (``FOR UPDATE SKIP LOCKED``). A job whose
    lease ran out is claimed again. Callers must end each job with ``finish_job`` or ``fail_job``."""
    async with db.session() as s:
        rows = await s.execute(
            text(
                "UPDATE jobs SET status = 'running', locked_at = now(), locked_by = :w, attempts = attempts + 1 "
                "WHERE id IN (SELECT id FROM jobs WHERE queue = ANY(:queues) AND ((status = 'queued' AND run_at <= now()) "
                "OR (status = 'running' AND locked_at < now() - make_interval(secs => :lease))) "
                "ORDER BY run_at FOR UPDATE SKIP LOCKED LIMIT :n) "
                "RETURNING id, name, payload, attempts, max_attempts"
            ),
            {"w": worker_id, "lease": lease.total_seconds(), "n": limit, "queues": list(queues)},
        )
        return [dict(r._mapping) for r in rows]


async def finish_job(db: Database, job_id: int) -> None:
    async with db.session() as s:
        await s.execute(
            text(
                "UPDATE jobs SET status = 'done', finished_at = now(), locked_at = NULL, locked_by = NULL, last_error = NULL WHERE id = :id"
            ),
            {"id": job_id},
        )


async def fail_job(db: Database, job: dict[str, Any], error: str, *, dead: bool = False) -> None:
    """Retry later with backoff, or mark dead once ``max_attempts`` is used up (or ``dead``)."""
    dead = dead or job["attempts"] >= job["max_attempts"]
    async with db.session() as s:
        await s.execute(
            text(
                "UPDATE jobs SET status = :st, last_error = :err, run_at = :at, locked_at = NULL, locked_by = NULL, "
                "finished_at = CASE WHEN :st = 'dead' THEN now() ELSE NULL END WHERE id = :id"
            ),
            {
                "st": "dead" if dead else "queued",
                "err": error[:1000],
                "at": datetime.now(timezone.utc) + backoff(job["attempts"]),
                "id": job["id"],
            },
        )


class Worker:
    """Claims due jobs with ``FOR UPDATE SKIP LOCKED``, so any number of workers can run side by side.

    Delivery is at-least-once: a job whose worker dies is picked up again after ``lease``. Write
    handlers so a repeat is harmless (use idempotency keys, as ``tsp.payments`` does).
    """

    def __init__(
        self,
        registry: JobRegistry,
        database: Database,
        *,
        concurrency: int = 4,
        poll_interval: float = 2.0,
        lease: timedelta = timedelta(minutes=10),
        worker_id: str | None = None,
        queues: Sequence[str] = ("default",),
    ):
        self.registry = registry
        self.db = database
        self.concurrency = concurrency
        self.poll = poll_interval
        self.lease = lease
        self.queues = list(queues)
        self.id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._sessions = async_sessionmaker(database.engine, expire_on_commit=False)

    async def sync_schedules(self) -> None:
        now = datetime.now(timezone.utc)
        async with self.db.session() as s:
            for name, (spec, job, payload) in self.registry.schedules.items():
                existing = await s.get(CronJob, name)
                if existing is None:
                    s.add(
                        CronJob(
                            name=name,
                            schedule=spec,
                            job_name=job,
                            payload=payload,
                            next_run_at=next_run(spec, now),
                        )
                    )
                elif (existing.schedule, existing.job_name, existing.payload) != (
                    spec,
                    job,
                    payload,
                ):
                    existing.schedule, existing.job_name, existing.payload = spec, job, payload
                    existing.next_run_at = next_run(spec, now)

    async def enqueue_due_cron(self) -> int:
        """Turn due schedules into jobs. The unique key makes two workers racing here harmless."""
        now = datetime.now(timezone.utc)
        n = 0
        async with self.db.session() as s:
            due = (
                (
                    await s.execute(
                        select(CronJob)
                        .where(CronJob.next_run_at <= now)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for c in due:
                if await enqueue(
                    s,
                    c.job_name,
                    c.payload,
                    unique_key=f"cron:{c.name}:{c.next_run_at.isoformat()}",
                ):
                    n += 1
                c.last_run_at, c.next_run_at = now, next_run(c.schedule, now)
        return n

    async def _claim(self, limit: int) -> list[dict[str, Any]]:
        return await claim_jobs(self.db, self.queues, self.id, self.lease, limit)

    async def _run(self, job: dict[str, Any]) -> None:
        handler = self.registry.handlers.get(job["name"])
        try:
            if handler is None:
                raise LookupError(f"no handler registered for job {job['name']!r}")
            # The handler's session commits only if it returns normally.
            async with self.db.session() as s:
                await handler(
                    JobContext(s, job["id"], job["attempts"], job["name"]), job["payload"]
                )
        except Exception as e:
            log.warning(
                "job %s (%s) failed on attempt %s: %s", job["id"], job["name"], job["attempts"], e
            )
            await fail_job(self.db, job, f"{type(e).__name__}: {e}", dead=handler is None)
            return
        await finish_job(self.db, job["id"])

    async def run_once(self, limit: int | None = None) -> int:
        """Claim and run up to ``limit`` due jobs (concurrently). Returns how many were attempted."""
        await self.enqueue_due_cron()
        jobs = await self._claim(limit or self.concurrency)
        await asyncio.gather(*(self._run(j) for j in jobs))
        return len(jobs)

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        await self.sync_schedules()
        while not stop.is_set():
            try:
                busy = await self.run_once()
            except Exception:
                log.exception("worker loop error; continuing")
                busy = 0
            if not busy:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll)


def worker_lifespan(registry: JobRegistry, **worker_kwargs: Any) -> Callable[[Any], Any]:
    """For ``create_app(background=[...])``: run a worker inside the API process.

    Stage 0 has no spare memory for a second container; the worker is a few asyncio tasks. It uses
    the app's own database, so the job and the request share one connection pool.
    """

    @asynccontextmanager
    async def run(app: Any) -> AsyncIterator[None]:
        db = getattr(app.state, "db", None)
        if db is None:  # no database configured: nothing to run
            yield
            return
        worker = Worker(registry, db, **worker_kwargs)
        app.state.job_worker = worker
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run_forever(stop), name="tsp-jobs-worker")
        try:
            yield
        finally:
            stop.set()
            try:
                await asyncio.wait_for(task, timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()

    return run
