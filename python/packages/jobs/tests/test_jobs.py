import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from tsp.jobs import (
    CronJob,
    Job,
    JobRegistry,
    Worker,
    enqueue,
    next_run,
    parse_schedule,
    worker_lifespan,
)


def make(db, **kw):
    reg = JobRegistry()
    calls = []

    @reg.job("ok")
    async def ok(ctx, payload):
        calls.append(("ok", payload, ctx.attempt))

    @reg.job("flaky")
    async def flaky(ctx, payload):
        calls.append(("flaky", ctx.attempt))
        if ctx.attempt < 3:
            raise RuntimeError("temporary")

    @reg.job("always_fails")
    async def bad(ctx, payload):
        raise ValueError("nope")

    return reg, Worker(reg, db, poll_interval=0.05, **kw), calls


async def rows(db):
    async with db.session() as s:
        return (await s.execute(select(Job).order_by(Job.id))).scalars().all()


async def test_enqueue_and_run(db):
    reg, w, calls = make(db)
    async with db.session() as s:
        await enqueue(s, "ok", {"x": 1})
    assert await w.run_once() == 1
    assert calls == [("ok", {"x": 1}, 1)]
    (job,) = await rows(db)
    assert (job.status, job.attempts, job.finished_at is not None) == ("done", 1, True)
    assert await w.run_once() == 0


async def test_enqueue_is_transactional(db):
    with pytest.raises(RuntimeError):
        async with db.session() as s:
            await enqueue(s, "ok")
            raise RuntimeError("the surrounding change failed")
    assert await rows(db) == []  # no job without the change that wanted it


async def test_unique_key_dedupes(db):
    async with db.session() as s:
        a = await enqueue(s, "ok", unique_key="welcome:u1")
        b = await enqueue(s, "ok", unique_key="welcome:u1")
    assert a is not None and b is None and len(await rows(db)) == 1


async def test_future_jobs_wait(db):
    reg, w, calls = make(db)
    async with db.session() as s:
        await enqueue(s, "ok", run_at=datetime.now(timezone.utc) + timedelta(hours=1))
    assert await w.run_once() == 0 and calls == []


async def test_failures_retry_with_backoff_then_succeed(db):
    reg, w, calls = make(db)
    async with db.session() as s:
        await enqueue(s, "flaky")
    await w.run_once()
    (job,) = await rows(db)
    assert job.status == "queued" and job.attempts == 1 and "temporary" in job.last_error
    assert job.run_at > datetime.now(timezone.utc) + timedelta(seconds=5)  # backed off
    async with db.session() as s:  # fast-forward the backoff
        await s.execute(text("UPDATE jobs SET run_at = now()"))
    await w.run_once()
    async with db.session() as s:
        await s.execute(text("UPDATE jobs SET run_at = now()"))
    await w.run_once()
    (job,) = await rows(db)
    assert (job.status, job.attempts, job.last_error) == ("done", 3, None)


async def test_jobs_that_keep_failing_end_up_dead_not_retried_forever(db):
    reg, w, _ = make(db)
    async with db.session() as s:
        await enqueue(s, "always_fails", max_attempts=2)
        await enqueue(s, "unknown_job", max_attempts=5)
    for _ in range(3):
        await w.run_once()
        async with db.session() as s:
            await s.execute(text("UPDATE jobs SET run_at = now() WHERE status = 'queued'"))
    jobs = {j.name: j for j in await rows(db)}
    assert jobs["always_fails"].status == "dead" and jobs["always_fails"].attempts == 2
    assert jobs["unknown_job"].status == "dead" and "no handler" in jobs["unknown_job"].last_error


async def test_a_crashed_worker_lease_expires(db):
    reg, w, calls = make(db, lease=timedelta(seconds=1))
    async with db.session() as s:
        await enqueue(s, "ok")
        await s.execute(
            text(
                "UPDATE jobs SET status = 'running', locked_at = now() - interval '5 seconds', locked_by = 'dead', attempts = 1"
            )
        )
    assert await w.run_once() == 1
    assert calls and calls[0][2] == 2 and (await rows(db))[0].status == "done"


async def test_many_workers_never_run_a_job_twice(db):
    reg = JobRegistry()
    seen = []

    @reg.job("count")
    async def count(ctx, payload):
        seen.append(payload["i"])
        await asyncio.sleep(0.01)

    async with db.session() as s:
        for i in range(40):
            await enqueue(s, "count", {"i": i})
    workers = [Worker(reg, db, concurrency=5, worker_id=f"w{i}") for i in range(4)]
    for _ in range(6):
        await asyncio.gather(*(w.run_once() for w in workers))
    assert sorted(seen) == list(range(40))  # every job exactly once


async def test_a_handler_that_fails_rolls_back_its_own_writes(db):
    reg = JobRegistry()

    @reg.job("half")
    async def half(ctx, payload):
        await ctx.session.execute(text("CREATE TABLE IF NOT EXISTS job_side_effect (n int)"))
        await ctx.session.execute(text("INSERT INTO job_side_effect VALUES (1)"))
        raise RuntimeError("fail after writing")

    async with db.session() as s:
        await s.execute(text("CREATE TABLE IF NOT EXISTS job_side_effect (n int)"))
        await enqueue(s, "half", max_attempts=1)
    await Worker(reg, db).run_once()
    async with db.session() as s:
        assert (await s.execute(text("select count(*) from job_side_effect"))).scalar_one() == 0
        await s.execute(text("DROP TABLE job_side_effect"))


def test_schedule_parsing():
    assert (
        parse_schedule("Every 15m") == "every 15m"
        and parse_schedule("daily 02:30") == "daily 02:30"
    )
    for bad in ("every 0m", "hourly", "daily 25:00", "every 5x", ""):
        with pytest.raises(ValueError):
            parse_schedule(bad)
    t = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    assert next_run("every 15m", t) == t + timedelta(minutes=15)
    assert next_run("daily 02:30", t) == datetime(2026, 10, 13, 2, 30, tzinfo=timezone.utc)
    assert next_run("daily 12:00", t) == datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    assert next_run("daily 10:00", t) == datetime(
        2026, 10, 13, 10, 0, tzinfo=timezone.utc
    )  # strictly after


async def test_cron_enqueues_once_even_if_workers_race(db):
    reg = JobRegistry()
    runs = []

    @reg.job("tick")
    async def tick(ctx, payload):
        runs.append(payload)

    reg.schedule("hourly-tick", "every 1h", "tick", {"a": 1})
    w1, w2 = Worker(reg, db), Worker(reg, db)
    await w1.sync_schedules()
    async with db.session() as s:
        await s.execute(text("UPDATE cron_jobs SET next_run_at = now() - interval '1 minute'"))
    made = await asyncio.gather(w1.enqueue_due_cron(), w2.enqueue_due_cron())
    assert sum(made) == 1
    await w1.run_once()
    assert runs == [{"a": 1}]
    async with db.session() as s:
        c = (await s.execute(select(CronJob))).scalar_one()
    assert (
        c.next_run_at > datetime.now(timezone.utc) + timedelta(minutes=50)
        and c.last_run_at is not None
    )
    assert await w1.enqueue_due_cron() == 0  # not due again until the next hour


async def test_changing_a_schedule_in_code_updates_the_table(db):
    reg = JobRegistry()
    reg.schedule("x", "every 1h", "tick")
    w = Worker(reg, db)
    await w.sync_schedules()
    reg.schedule("x", "every 5m", "tick")
    await w.sync_schedules()
    async with db.session() as s:
        assert (await s.execute(select(CronJob.schedule))).scalar_one() == "every 5m"


async def test_worker_runs_inside_an_app_lifespan(db):
    from fastapi import FastAPI

    reg = JobRegistry()
    done = asyncio.Event()

    @reg.job("ping")
    async def ping(ctx, payload):
        done.set()

    app = FastAPI()
    app.state.db = db
    async with worker_lifespan(reg, poll_interval=0.05)(app):
        async with db.session() as s:
            await enqueue(s, "ping")
        await asyncio.wait_for(done.wait(), timeout=5)
    assert (await rows(db))[0].status == "done"
