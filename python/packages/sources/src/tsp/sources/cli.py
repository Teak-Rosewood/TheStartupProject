"""``tsp-sources``: manage and run sources from a terminal.

Environment: ``DATABASE_URL``; ``CACHE_URL`` (default none); ``TSP_SOURCES_CONNECTORS`` (comma
separated modules that register connectors); ``TSP_SOURCES_DIR`` (local raw store) or
``TSP_SOURCES_BUCKET`` (S3). API keys for strategies are read from ``TSP_SOURCES_SECRET_<NAME>``
and passed to connectors as config key ``<name>``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib
import os
import sys
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from tsp.cache import cache_from_url
from tsp.crawl import HttpFetcher
from tsp.db import Database

from .checks import SourceChecks
from .models import Run, Source
from .registry import validate_strategies
from .runner import SourceRunner
from .store import LocalRawStore, RawStore, S3RawStore

_LIST_COLS = ("languages", "allowed_uses")


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(";") if v.strip()]


def parse_source_row(row: dict[str, str]) -> dict[str, Any]:
    """One CSV row to column values. Blank cells are left out so re-importing never wipes a field."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        v = (v or "").strip()
        if not v or k is None:
            continue
        if k in _LIST_COLS:
            out[k] = _split(v)
        elif k == "strategies":
            out[k] = validate_strategies(_split(v))
        elif k == "rate_limit_rps":
            out[k] = float(v)
        elif k == "paid_cap_monthly":
            out[k] = Decimal(v)
        elif k == "enabled":
            out[k] = v.lower() in ("1", "true", "yes")
        else:
            out[k] = v
    for need in ("key", "name", "url"):
        if need not in out:
            raise ValueError(f"row is missing {need!r}: {row}")
    return out


async def import_csv(db: Database, path: str) -> int:
    with open(path, newline="", encoding="utf-8") as f:
        rows = [parse_source_row(r) for r in csv.DictReader(f)]
    async with db.session() as s:
        for values in rows:
            stmt = insert(Source).values(**values)
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["key"], set_={k: v for k, v in values.items() if k != "key"}
                )
            )
    return len(rows)


def _store() -> RawStore:
    if bucket := os.environ.get("TSP_SOURCES_BUCKET"):
        return S3RawStore(bucket)
    return LocalRawStore(os.environ.get("TSP_SOURCES_DIR", "./sources-data"))


def _secrets() -> dict[str, str]:
    prefix = "TSP_SOURCES_SECRET_"
    return {k[len(prefix) :].lower(): v for k, v in os.environ.items() if k.startswith(prefix)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tsp-sources")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("status")
    imp = sub.add_parser("import")
    imp.add_argument("csv")
    chk = sub.add_parser("check")
    chk.add_argument("--source")
    chk.add_argument("--all", action="store_true")
    for name in ("sync", "backfill"):
        s = sub.add_parser(name)
        s.add_argument("--source", required=True)
        s.add_argument("--stream")
        s.add_argument("--since", required=name == "backfill")
        s.add_argument("--strategy")
        if name == "sync":
            s.add_argument("--dry-run", action="store_true")
    rec = sub.add_parser("reconcile")
    rec.add_argument("--source", required=True)
    rec.add_argument("--stream", required=True)
    rec.add_argument("--sample", type=int, default=50)
    args = p.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    for mod in filter(None, os.environ.get("TSP_SOURCES_CONNECTORS", "").split(",")):
        importlib.import_module(mod.strip())
    return asyncio.run(_run(args, url))


async def _run(args: argparse.Namespace, url: str) -> int:
    db = Database(url)
    cache = cache_from_url(os.environ.get("CACHE_URL"))
    http = HttpFetcher()
    try:
        if args.cmd == "import":
            print(f"imported {await import_csv(db, args.csv)} sources")
        elif args.cmd == "list":
            async with db.session() as s:
                for r in (await s.execute(select(Source).order_by(Source.key))).scalars():
                    print(
                        f"{r.key:24} {'on ' if r.enabled else 'OFF'} {','.join(r.strategies):32} {r.url}"
                    )
        elif args.cmd == "status":
            async with db.session() as s:
                last = (
                    select(Run.source_id, func.max(Run.started_at).label("at"))
                    .group_by(Run.source_id)
                    .subquery()
                )
                q = (
                    select(
                        Source.key,
                        Run.status,
                        Run.strategy_used,
                        Run.started_at,
                        Run.items_new,
                        Run.errors,
                    )
                    .join(last, last.c.source_id == Source.id, isouter=True)
                    .join(
                        Run,
                        (Run.source_id == last.c.source_id) & (Run.started_at == last.c.at),
                        isouter=True,
                    )
                    .order_by(Source.key)
                )
                for key, status, strat, at, new, errs in (await s.execute(q)).all():
                    print(
                        f"{key:24} {status or 'never run':8} {strat or '-':10} {at or '-'} new={new} errors={errs}"
                    )
        elif args.cmd == "check":
            checks = SourceChecks(db, http, cache)
            async with db.session() as s:
                keys = (
                    [args.source]
                    if args.source
                    else (
                        (await s.execute(select(Source.key).where(Source.enabled.is_(True))))
                        .scalars()
                        .all()
                        if args.all
                        else []
                    )
                )
            if not keys:
                print("give --source KEY or --all", file=sys.stderr)
                return 2
            for key in keys:
                res_c = await checks.check(key)
                flag = " DISABLED: " + "; ".join(res_c.reasons) if res_c.disabled else ""
                print(f"{key}: reachable={res_c.reachable} robots_ok={res_c.robots_ok}{flag}")
        else:
            runner = SourceRunner(db, _store(), cache, http, config=_secrets())
            if args.cmd == "reconcile":
                rep = await runner.reconcile(args.source, args.stream, args.sample)
                print(
                    f"{rep.strategies[0]} vs {rep.strategies[1]}: compared={rep.compared} "
                    f"only_first={len(rep.only_in_first)} only_second={len(rep.only_in_second)} "
                    f"different={len(rep.different)}"
                )
                return 0 if rep.consistent else 1
            res = await runner.sync(
                args.source,
                args.stream,
                args.since,
                strategy=args.strategy,
                dry_run=getattr(args, "dry_run", False),
            )
            for sr in res.streams:
                print(
                    f"{args.source}/{sr.stream}: {sr.status} via {sr.strategy_used} "
                    f"new={sr.items_new} updated={sr.items_updated} errors={sr.errors}"
                    + (f" ({sr.error_summary})" if sr.error_summary else "")
                )
            return 0 if res.status != "failed" else 1
        return 0
    finally:
        await http.aclose()
        await cache.close()
        await db.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
