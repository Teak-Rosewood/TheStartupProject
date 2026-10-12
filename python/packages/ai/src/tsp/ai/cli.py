"""``tsp-ai`` command line: ``verify`` provider models, ``serve`` an OpenAI-compatible API."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from pathlib import Path

from .catalog import DEFAULT_CATALOG_PATH, ProviderResult, Status


def _summary_line(r: ProviderResult) -> str:
    ok = len(r.ok_models)
    if r.key_status is Status.OK and not r.models:
        return f"{r.platform:<12} key ok, listed {r.listed}, nothing probed (no free models; omit --free-only)"
    if r.key_status is not Status.OK and not r.models:
        return f"{r.platform:<12} {r.key_status.value:<17} {r.error or ''}"[:160]
    reasons = Counter(m.status.value for m in r.models if m.status is not Status.OK)
    top = ", ".join(f"{k}={v}" for k, v in reasons.most_common(3))
    return f"{r.platform:<12} ok {ok}/{len(r.models)} (listed {r.listed})  {top}"


def _cmd_verify(args: argparse.Namespace) -> int:
    from .client import LLMClient
    from .discovery import verify

    env_file = Path(args.env_file)
    client = LLMClient.from_env(env_file if env_file.exists() else None)
    platforms = args.provider or None
    if platforms:
        unknown = [p for p in platforms if p not in client._router._providers]
        if unknown:
            print(f"no API key found for: {', '.join(unknown)}", file=sys.stderr)
            return 2

    async def run():
        try:
            return await verify(
                client,
                platforms,
                free_only=args.free_only,
                concurrency=args.concurrency,
                max_models=args.max_models,
                on_provider=lambda r: print(_summary_line(r), flush=True),
            )
        finally:
            await client.close()

    catalog = asyncio.run(run())
    if args.verbose:
        print()
        for r in catalog.providers.values():
            for m in r.models:
                lat = f"{m.latency_ms:.0f}ms" if m.latency_ms else "-"
                print(
                    f"{r.platform:<12} {m.status.value:<17} {lat:>7}  {m.id}  {m.error or ''}"[:200]
                )
    path = catalog.save(args.out)
    working = sum(1 for r in catalog.providers.values() if r.ok_models)
    print(f"\n{working}/{len(catalog.providers)} providers have working models. Catalog: {path}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from .server import create_app
    except ImportError:
        print("server extra missing: pip install 'tsp-ai[server]'", file=sys.stderr)
        return 2
    from .client import LLMClient

    key = os.environ.get("TSP_AI_SERVER_KEY")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not key and not args.insecure:
        print(
            "refusing to listen on a non-loopback host without TSP_AI_SERVER_KEY "
            "(pass --insecure to override)",
            file=sys.stderr,
        )
        return 2
    env_file = Path(args.env_file)
    client = LLMClient.from_env(env_file if env_file.exists() else None, catalog=args.catalog)
    uvicorn.run(create_app(client, api_key=key), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tsp-ai")
    sub = parser.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="probe every model of every configured provider")
    v.add_argument("--env-file", default=".env")
    v.add_argument("--provider", action="append", help="only this provider (repeatable)")
    v.add_argument("--free-only", action="store_true", help="probe only models known to be free")
    v.add_argument("--concurrency", type=int, default=2, help="parallel probes per provider")
    v.add_argument("--max-models", type=int, help="cap probes per provider")
    v.add_argument("--out", default=str(DEFAULT_CATALOG_PATH))
    v.add_argument("-v", "--verbose", action="store_true", help="print every model result")
    v.set_defaults(func=_cmd_verify)

    s = sub.add_parser("serve", help="run an OpenAI-compatible HTTP server")
    s.add_argument("--env-file", default=".env")
    s.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH))
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--insecure", action="store_true")
    s.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
