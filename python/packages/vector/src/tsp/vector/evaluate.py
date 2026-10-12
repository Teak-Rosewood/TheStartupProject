"""Retrieval quality: of the questions you can answer from your documents, how often and how high
does the right document come back? Labels are document names, because chunk ids change whenever
the chunk size does."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

Search = Callable[[str, int], Awaitable[list[dict[str, Any]]]]  # (question, k) -> hits with "title"


@dataclass(frozen=True)
class Scores:
    questions: int
    recall_at_k: float  # share of questions whose expected document is in the top k
    mrr: float  # mean of 1/rank of the expected document (0 when absent)

    def drop_from(self, baseline: Scores) -> float:
        """Largest fall in either score, in percentage points (negative when better)."""
        return max(baseline.recall_at_k - self.recall_at_k, baseline.mrr - self.mrr) * 100


async def evaluate(search: Search, questions: list[dict[str, str]], k: int = 5) -> Scores:
    found, rr = 0, 0.0
    for q in questions:
        titles: list[str] = []
        for h in await search(q["question"], k):
            if h["title"] not in titles:
                titles.append(h["title"])
        if q["expected"] in titles:
            found += 1
            rr += 1.0 / (titles.index(q["expected"]) + 1)
    n = len(questions) or 1
    return Scores(len(questions), found / n, rr / n)
