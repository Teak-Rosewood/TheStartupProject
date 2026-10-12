"""``tsp-pipeline prompts push <dir>`` and ``prompts activate <key> <version>``."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from tsp.db import Database

from .prompts import PromptRegistry


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tsp-pipeline")
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("prompts", help="manage the prompt registry").add_subparsers(
        dest="sub", required=True
    )
    push = pr.add_parser("push", help="load prompts/*.md; a new version only when the body changed")
    push.add_argument("dir")
    push.add_argument("--by", default=os.environ.get("USER"))
    act = pr.add_parser("activate", help="make a version the one workflows use")
    act.add_argument("key")
    act.add_argument("version", type=int)
    args = p.parse_args(argv)
    if not args.database_url:
        print("set DATABASE_URL or pass --database-url", file=sys.stderr)
        return 2

    async def go() -> int:
        db = Database(args.database_url)
        try:
            async with db.session() as s:
                reg = PromptRegistry()
                if args.sub == "push":
                    for key, (version, created) in (
                        await reg.push_dir(s, args.dir, args.by)
                    ).items():
                        print(f"{key}@{version} {'created' if created else 'unchanged'}")
                else:
                    await reg.activate(s, args.key, args.version)
                    print(f"{args.key}@{args.version} is active")
            return 0
        except (LookupError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        finally:
            await db.dispose()

    return asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(main())
