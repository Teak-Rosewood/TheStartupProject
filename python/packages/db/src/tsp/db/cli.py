"""``tsp-db migrate``: apply Alembic migrations using DATABASE_URL."""

from __future__ import annotations

import argparse
import os
import sys

from .migrate import upgrade


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tsp-db")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate", help="apply migrations up to head")
    m.add_argument("--url", default=os.environ.get("DATABASE_URL"))
    m.add_argument("--script-location", default=os.environ.get("TSP_MIGRATIONS", "migrations"))
    m.add_argument("--revision", default=None, help="default: head (heads when modules are given)")
    m.add_argument(
        "--module",
        action="append",
        default=[],
        help="installed tsp module whose migrations to include, e.g. tsp.audit (repeatable; "
        "default from TSP_MIGRATE_MODULES, comma separated)",
    )
    args = p.parse_args(argv)
    if not args.url:
        print("DATABASE_URL (or --url) is required", file=sys.stderr)
        return 2
    modules = args.module or [m for m in os.environ.get("TSP_MIGRATE_MODULES", "").split(",") if m]
    upgrade(args.url, args.script_location, args.revision, modules=modules)
    print(f"migrated to {args.revision or 'head'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
