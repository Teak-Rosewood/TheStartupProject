from tsp.jobs import Worker


async def drain(db, registry, rounds=50, concurrency=4):
    """Run queued jobs until none are due (jobs enqueue more jobs as they go)."""
    worker = Worker(registry, db, concurrency=concurrency)
    for _ in range(rounds):
        if await worker.run_once() == 0:
            return
    raise AssertionError("jobs did not settle")
