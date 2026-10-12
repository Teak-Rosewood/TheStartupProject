"""``tsp-vector``: index files, search, re-embed and measure retrieval quality from the command line.
Needs DATABASE_URL (or the TSP_* equivalents) and at least one embedding provider key."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from tsp.audit import Actor
from tsp.db import Database

from .config import VectorConfig
from .evaluate import Scores, evaluate
from .extract import UnsupportedDocument, extract
from .pg import PgVectorStore
from .service import VectorService, ai_embedder

ACTOR = Actor(None, "cli")


def _service() -> tuple[VectorService, Database]:
    from tsp.ai import LLMClient
    from tsp.server import Settings

    url = Settings.from_env().database_url
    if not url:
        sys.exit("set DATABASE_URL (or TSP_DATABASE_URL)")
    db = Database(url)
    llm = LLMClient.from_env(catalog=os.environ.get("TSP_AI_CATALOG"))
    for entry in filter(None, os.environ.get("TSP_AI_EMBEDDING_MODELS", "").split(",")):
        platform, model, *dims = entry.strip().split(":")
        if platform in llm._router._providers:
            llm._router.set_embedding_models(platform, {model: int(dims[0]) if dims else None})
    cfg = VectorConfig(
        dims=int(os.environ.get("VECTOR_DIMS", "1024")),
        embed_model=os.environ.get("VECTOR_EMBED_MODEL", "text-embedding-3-small"),
    )
    return VectorService(PgVectorStore(db), ai_embedder(llm), db, cfg), db


async def _ensure_collection(svc: VectorService, org: str, name: str) -> None:
    async with svc._session() as s:
        if not any(c.name == name for c in await svc.collections(s, org)):
            await svc.create_collection(s, org, name)


async def ingest(
    svc: VectorService, org: str, collection: str, path: Path
) -> list[tuple[str, str]]:
    await _ensure_collection(svc, org, collection)
    files = sorted(p for p in ([path] if path.is_file() else path.rglob("*")) if p.is_file())
    out = []
    for f in files:
        try:
            body = extract(f.read_bytes(), "", f.name)
        except UnsupportedDocument as e:
            out.append((f.name, f"skipped: {e}"))
            continue
        async with svc._session() as s:
            doc = await svc.add_document(s, org, collection, text_=body, title=f.stem)
        await svc.ingest(doc.id)
        async with svc._session() as s:
            status = (await svc.documents(s, org, collection))[0]
        out.append((f.name, status.status + (f": {status.error}" if status.error else "")))
    return out


def _scores_json(s: Scores) -> dict[str, Any]:
    return {
        "questions": s.questions,
        "recall_at_k": round(s.recall_at_k, 4),
        "mrr": round(s.mrr, 4),
    }


async def run(args: argparse.Namespace) -> int:
    svc, db = _service()
    try:
        if args.cmd == "ingest":
            for name, status in await ingest(svc, args.org, args.collection, Path(args.path)):
                print(f"{status:<10} {name}")
        elif args.cmd == "search":
            async with svc._session() as s:
                for h in await svc.search(s, args.org, args.collection, args.query, k=args.k):
                    print(f"{h['score']:.4f}  {h['title']}: {h['content'][:100]!r}")
        elif args.cmd == "reembed":
            async with svc._session() as s:
                col = await svc.collection(s, args.org, args.collection)
            await svc.reembed(col.id, args.model)
            print(f"{args.collection} now uses {args.model}")
        elif args.cmd == "eval":
            questions = json.loads(Path(args.questions).read_text())

            async def search(q: str, k: int) -> list[dict[str, Any]]:
                async with svc._session() as s:
                    return await svc.search(s, args.org, args.collection, q, k=k)

            scores = await evaluate(search, questions, args.k)
            print(json.dumps(_scores_json(scores)))
            if args.baseline and Path(args.baseline).exists():
                b = json.loads(Path(args.baseline).read_text())
                drop = scores.drop_from(Scores(b["questions"], b["recall_at_k"], b["mrr"]))
                print(f"change vs baseline: {-drop:+.1f} points")
                if drop > args.max_drop:
                    print(f"FAIL: dropped more than {args.max_drop} points", file=sys.stderr)
                    return 1
            if args.save_baseline:
                Path(args.save_baseline).write_text(json.dumps(_scores_json(scores)))
    finally:
        await db.dispose()
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tsp-vector")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--org", required=True)
        sp.add_argument("--collection", required=True)
        return sp

    common(sub.add_parser("ingest", help="index a file or folder")).add_argument(
        "--path", required=True
    )
    s = common(sub.add_parser("search", help="search a collection"))
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)
    common(
        sub.add_parser("reembed", help="switch a collection to another embedding model")
    ).add_argument("--model", required=True)
    e = common(sub.add_parser("eval", help="recall@k and MRR over labelled questions"))
    e.add_argument("--questions", required=True)
    e.add_argument("-k", type=int, default=5)
    e.add_argument(
        "--baseline", help="JSON from --save-baseline; fail when scores fall past --max-drop"
    )
    e.add_argument("--max-drop", type=float, default=5.0, help="points of recall or MRR")
    e.add_argument("--save-baseline")
    sys.exit(asyncio.run(run(p.parse_args(argv))))
