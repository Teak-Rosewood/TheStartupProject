from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass

from .errors import FetchError

MAX_XML_BYTES = 50_000_000  # sitemap spec limit is 50 MB uncompressed
MAX_ENTRIES = 50_000
_DTD = re.compile(rb"<!(DOCTYPE|ENTITY)", re.IGNORECASE)


@dataclass(frozen=True)
class SitemapEntry:
    loc: str
    lastmod: str | None = None
    is_sitemap: bool = False  # True: entry from a sitemap index, fetch and parse it too


def _gunzip(data: bytes) -> bytes:
    d = zlib.decompressobj(31)
    out = d.decompress(data, MAX_XML_BYTES + 1)
    if len(out) > MAX_XML_BYTES or d.unconsumed_tail:
        raise FetchError("sitemap too large when decompressed")
    return out


def parse_sitemap(xml: bytes | str) -> list[SitemapEntry]:
    """Parse a ``urlset`` or ``sitemapindex``; accepts gzip. Rejects DTDs (entity expansion)."""
    data = xml.encode() if isinstance(xml, str) else xml
    if data[:2] == b"\x1f\x8b":
        data = _gunzip(data)
    if len(data) > MAX_XML_BYTES:
        raise FetchError("sitemap too large")
    if _DTD.search(data):
        raise FetchError("sitemap contains a DTD; refusing to parse")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise FetchError(f"invalid sitemap XML: {e}") from e
    is_index = root.tag.rsplit("}", 1)[-1] == "sitemapindex"
    entries: list[SitemapEntry] = []
    for node in root:
        fields = {c.tag.rsplit("}", 1)[-1]: (c.text or "").strip() for c in node}
        if fields.get("loc"):
            entries.append(SitemapEntry(fields["loc"], fields.get("lastmod") or None, is_index))
        if len(entries) >= MAX_ENTRIES:
            break
    return entries
