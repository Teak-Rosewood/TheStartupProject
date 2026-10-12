"""``tsp-pipeline prompts push|activate`` and ``tsp-pipeline eval <suite> --prompt key@version``."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from tsp.db import Database

from .prompts import PromptRegistry


def main(argv: list[str] | None = None, llm: Any = None) -> int:
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
    ev = sub.add_parser("eval", help="score a prompt version on an eval suite")
    ev.add_argument("suite")
    ev.add_argument("--prompt", required=True, help="key@version (or key for the active version)")
    ev.add_argument("--evals-dir", default="evals")
    ev.add_argument("--model", help="model for the prompt (default: the prompt's model_hint)")
    ev.add_argument("--scorer", choices=["exact", "python", "judge"])
    ev.add_argument("--judge-model", help="model that grades, for the judge scorer")
    ev.add_argument("--threshold", type=float, default=0.0, help="fail below this mean score")
    ev.add_argument("--max-drop", type=float, help="fail if this far below the active version")
    ev.add_argument(
        "--no-compare", action="store_true", help="do not also score the active version"
    )
    ev.add_argument("--concurrency", type=int, default=4)
    ev.add_argument("--env-file", default=".env")
    args = p.parse_args(argv)
    if not args.database_url:
        print("set DATABASE_URL or pass --database-url", file=sys.stderr)
        return 2

    async def go() -> int:
        db = Database(args.database_url)
        try:
            async with db.session() as s:
                reg = PromptRegistry()
                if args.cmd == "eval":
                    return await _eval(args, s, llm)
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


async def _eval(args: argparse.Namespace, s: Any, llm: Any) -> int:
    from .evals import load_suite, prompt_target, run_eval

    if llm is None:
        from tsp.ai import LLMClient

        env = Path(args.env_file)
        llm = LLMClient.from_env(env if env.exists() else None)
    suite = load_suite(args.evals_dir, args.suite)
    report = await run_eval(
        s, suite, args.prompt, prompt_target(llm, args.model),
        scorer=args.scorer, llm=llm, judge_model=args.judge_model or args.model, model=args.model,
        threshold=args.threshold, max_drop=args.max_drop, compare=not args.no_compare,
        concurrency=args.concurrency,
    )  # fmt: skip
    print(f"{report.suite} {report.prompt} [{report.scorer}] score {report.score:.3f}")
    if report.baseline_score is not None:
        print(f"  active {report.baseline}: {report.baseline_score:.3f}")
    for r in report.results:
        if r["score"] < 1:
            print(f"  case {r['id']}: {r['score']:.2f} {r['error'] or r['reason']}")
    for why in report.reasons:
        print(f"FAIL: {why}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
