from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    title: str | None
    markdown: str
    links: list[str]
    content_hash: str
    etag: str | None
    last_modified: str | None
    fetched_at: datetime
    content_type: str
    fetcher: str
    canonical_url: str | None = None


@dataclass
class NotModified:
    url: str
    etag: str | None = None
    last_modified: str | None = None


class Fetcher(Protocol):
    async def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> Page | NotModified: ...


def content_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode()).hexdigest()
