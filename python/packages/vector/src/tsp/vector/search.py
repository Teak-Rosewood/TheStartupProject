from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .lang import detect_lang
from .store import Hit

RRF_K = 60
Reranker = Callable[[str, list[Hit]], Awaitable[list[Hit]]]


def rrf(rankings: list[list[Hit]], k: int = RRF_K) -> list[Hit]:
    """Reciprocal-rank fusion: a hit's score is the sum of 1/(k+rank) over the lists it appears in."""
    scores: dict[str, float] = {}
    first: dict[str, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit["id"]] = scores.get(hit["id"], 0.0) + 1.0 / (k + rank)
            first.setdefault(hit["id"], hit)
    return [
        {**first[i], "score": s} for i, s in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


async def hybrid_search(
    store: Any,
    org_id: str,
    collection_id: str,
    query: str,
    vector: list[float],
    *,
    k: int = 10,
    filter: dict[str, Any] | None = None,
    model: str | None = None,
    candidates: int = 4,
    rerank: Reranker | None = None,
) -> list[Hit]:
    """Vector top ``k*candidates`` plus full-text top ``k*candidates`` (when the store has full-text
    search), fused with RRF, optionally re-ranked, cut to ``k``."""
    n = k * candidates
    rankings = [await store.query(org_id, collection_id, vector, n, filter, model)]
    if hasattr(store, "text_query") and query.strip():
        rankings.append(
            await store.text_query(
                org_id, collection_id, query, n, filter, model, detect_lang(query)
            )
        )
    hits = rrf(rankings)[: max(k, n // 2) if rerank else k]
    if rerank:
        hits = (await rerank(query, hits))[:k]
    return hits
