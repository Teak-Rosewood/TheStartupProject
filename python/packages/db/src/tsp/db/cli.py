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
    m.add_argument("--revision", default="head")
    args = p.parse_args(argv)
    if not args.url:
        print("DATABASE_URL (or --url) is required", file=sys.stderr)
        return 2
    upgrade(args.url, args.script_location, args.revision)
    print(f"migrated to {args.revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
