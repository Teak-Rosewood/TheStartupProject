"""``tsp-compliance render-legal``: fill the legal templates and write documents.json."""

from __future__ import annotations

import argparse
import sys

from .render import RenderError, render_legal


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tsp-compliance")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render-legal", help="fill templates from company.toml")
    r.add_argument("--config", required=True)
    r.add_argument("--src", default="docs/legal")
    r.add_argument("--out", required=True)
    r.add_argument("--url-prefix", default="/legal")
    r.add_argument(
        "--strict",
        action="store_true",
        help="fail on '(verify)' / optional markers (use before launch)",
    )
    args = p.parse_args(argv)
    try:
        manifest, warnings = render_legal(
            args.config, args.src, args.out, url_prefix=args.url_prefix
        )
    except (RenderError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for doc_id, items in warnings.items():
        for w in items:
            print(f"warning: {doc_id}: {w}", file=sys.stderr)
    print(f"rendered {len(manifest.documents)} documents to {args.out}")
    return 1 if (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
