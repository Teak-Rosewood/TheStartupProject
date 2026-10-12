"""``tsp-jobs worker``: a standalone worker process (use it when you outgrow the in-API worker)."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import sys

from tsp.db import Database

from .registry import JobRegistry
from .worker import Worker


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tsp-jobs")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worker", help="run jobs until stopped")
    w.add_argument(
        "--registry",
        default=os.environ.get("TSP_JOBS_REGISTRY"),
        help="module:attribute of a JobRegistry",
    )
    w.add_argument("--url", default=os.environ.get("DATABASE_URL"))
    w.add_argument("--concurrency", type=int, default=4)
    w.add_argument(
        "--queue",
        action="append",
        help="queue to run (repeatable; default: the default queue)",
    )
    args = p.parse_args(argv)
    if not args.url or not args.registry:
        print(
            "DATABASE_URL and --registry (or TSP_JOBS_REGISTRY=module:attr) are required",
            file=sys.stderr,
        )
        return 2
    module, _, attr = args.registry.partition(":")
    registry = getattr(importlib.import_module(module), attr or "registry")
    if not isinstance(registry, JobRegistry):
        print(f"{args.registry} is not a JobRegistry", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO)

    async def go() -> None:
        db = Database(args.url)
        try:
            await Worker(
                registry, db, concurrency=args.concurrency, queues=args.queue or ["default"]
            ).run_forever()
        finally:
            await db.dispose()

    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
