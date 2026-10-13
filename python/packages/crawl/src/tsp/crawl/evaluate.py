"""Score Markdown extraction against hand-written references.

Three numbers, each 0 to 1: how much of the reference text survived (``recall``), how much page
chrome (cookie banners, newsletter boxes, footers...) was removed (``boilerplate_removed``), and
whether tables kept their cells and their table shape (``table_retention``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = re.compile(r"[^\W_]{3,}", re.UNICODE)
_SEPARATOR = re.compile(r"^\|?[\s:|-]+\|?$")


@dataclass(frozen=True)
class ExtractionScore:
    recall: float
    boilerplate_removed: float
    table_retention: float


def _words(text: str) -> set[str]:
    return {w.casefold() for w in _WORD.findall(text)}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[|*_`#>\-]+", " ", text).casefold().split())


def table_lines(markdown: str) -> list[str]:
    return [
        ln.strip()
        for ln in markdown.splitlines()
        if ln.strip().startswith("|") and not _SEPARATOR.match(ln.strip())
    ]


def _cells(lines: list[str]) -> list[str]:
    return [c.strip() for ln in lines for c in ln.strip().strip("|").split("|") if c.strip()]


def score_extraction(markdown: str, reference: str, boilerplate: list[str]) -> ExtractionScore:
    """Compare ``markdown`` (what the extractor produced) with ``reference`` (what a person would keep).

    ``boilerplate`` lists phrases that appear on the page but must not appear in the output.
    """
    ref_words, got_words = _words(reference), _words(markdown)
    recall = len(ref_words & got_words) / len(ref_words) if ref_words else 1.0
    text = _norm(markdown)
    leaked = sum(1 for phrase in boilerplate if _norm(phrase) in text)
    removed = 1 - leaked / len(boilerplate) if boilerplate else 1.0
    ref_rows, got_rows = table_lines(reference), table_lines(markdown)
    if not ref_rows:
        return ExtractionScore(recall, removed, 1.0)
    cells = _cells(ref_rows)
    found = sum(1 for c in cells if _norm(c) in text) / len(cells)
    shape = min(
        1.0, len(got_rows) / len(ref_rows)
    )  # cells that survive as loose text are not a table
    return ExtractionScore(recall, removed, found * shape)
