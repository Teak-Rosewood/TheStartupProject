"""Split text into overlapping chunks that respect headings and paragraphs."""

from __future__ import annotations

import re
from dataclasses import dataclass

CHARS_PER_TOKEN = 4  # a rough, model-independent estimate; chunk sizes need not be exact
_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.M)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    content: str
    tokens: int
    heading: str | None = None


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def _pieces(text: str, limit: int) -> list[tuple[str, str | None]]:
    """Paragraphs tagged with the heading they sit under; oversize paragraphs are cut at sentences."""
    out: list[tuple[str, str | None]] = []
    heading: str | None = None
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        m = _HEADING.match(para)
        if m:
            heading = m.group(1).strip()
        if len(para) <= limit:
            out.append((para, heading))
            continue
        buf = ""
        for sent in _SENTENCE.split(para):
            while len(sent) > limit:  # no sentence boundary: cut hard
                if buf:
                    out.append((buf, heading))
                    buf = ""
                out.append((sent[:limit], heading))
                sent = sent[limit:]
            if buf and len(buf) + 1 + len(sent) > limit:
                out.append((buf, heading))
                buf = sent
            else:
                buf = f"{buf} {sent}".strip()
        if buf:
            out.append((buf, heading))
    return out


def chunk(
    text: str, *, target_tokens: int = 400, overlap: int = 60, headings: bool = True
) -> list[Chunk]:
    """Pack paragraphs up to ``target_tokens``; each chunk repeats the tail of the previous one
    (``overlap`` tokens, at a paragraph boundary) so a fact that straddles a cut is still found.
    With ``headings`` a chunk that does not start with its section heading gets it prepended."""
    limit = max(1, target_tokens * CHARS_PER_TOKEN)
    keep = overlap * CHARS_PER_TOKEN
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_len = 0
    cur_heading: str | None = None
    fresh = (
        False  # paragraphs added since the last flush (the carried overlap alone is not a chunk)
    )

    def flush() -> None:
        nonlocal cur, cur_len, fresh
        if not cur or not fresh:
            return
        fresh = False
        body = "\n\n".join(cur)
        if headings and cur_heading and not body.lstrip().startswith("#"):
            body = f"# {cur_heading}\n\n{body}"
        chunks.append(Chunk(len(chunks), body, estimate_tokens(body), cur_heading))
        tail: list[str] = []
        size = 0
        for para in reversed(cur):  # carry whole trailing paragraphs that fit the overlap budget
            if size + len(para) > keep:
                break
            tail.insert(0, para)
            size += len(para)
        cur, cur_len = tail, size

    for para, heading in _pieces(text, limit):
        if cur_len and cur_len + len(para) > limit:
            flush()
        if not cur:
            cur_heading = heading
        cur.append(para)
        fresh = True
        cur_len += len(para)
        cur_heading = cur_heading or heading
    flush()
    return chunks
