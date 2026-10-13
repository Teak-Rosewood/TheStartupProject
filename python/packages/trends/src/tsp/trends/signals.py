"""Signal plug-ins. Each ``collect`` returns raw points; scoring happens later from what is stored."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import quote

from sqlalchemy import select
from tsp.crawl import CrawlError, HttpFetcher
from tsp.db import Database
from tsp.sources.models import Item as SourceItem
from tsp.sources.models import Source

_DTD = re.compile(rb"<!(DOCTYPE|ENTITY)", re.IGNORECASE)
_STOP = frozenset(
    "a an and are as at be by for from has have how in is it its of on or that the this to was "
    "what when where which who why will with your you our we i my not no new best top can do does "
    "vs via about after before into more most than then there these they their them".split()
)


@dataclass(frozen=True)
class Topic:
    id: str
    key: str
    label: str
    keywords: tuple[str, ...]
    langs: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        low = text.lower()
        return any(re.search(rf"(?<!\w){re.escape(k.lower())}(?!\w)", low) for k in self.keywords)


@dataclass
class SignalPoint:
    at: datetime
    value: float
    topic_id: str | None = None  # None: not about any known topic; ``label`` names the term
    label: str | None = None
    region: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrendsContext:
    http: HttpFetcher
    db: Database
    config: Mapping[str, str] = field(default_factory=dict)

    async def get_text(self, url: str) -> str:
        raw = await self.http.fetch_raw(url)
        if raw.status >= 400:
            raise SignalUnavailable(f"HTTP {raw.status} for {url}")
        return raw.body.decode("utf-8", errors="replace")


class SignalUnavailable(Exception):
    """This rung or signal cannot answer now; a ladder moves to the next one."""


class Signal(Protocol):
    name: str

    async def collect(
        self, ctx: TrendsContext, topics: Sequence[Topic], since: datetime
    ) -> list[SignalPoint]: ...


# -- feed parsing ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedItem:
    title: str
    link: str
    at: datetime


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        d = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def parse_feed(xml: str | bytes) -> list[FeedItem]:
    """RSS 2.0 and Atom. DTDs are refused (entity expansion); items without a date are dropped."""
    data = xml.encode() if isinstance(xml, str) else xml
    if _DTD.search(data):
        raise SignalUnavailable("feed contains a DTD; refusing to parse")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise SignalUnavailable(f"invalid feed: {e}") from e
    items = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        f: dict[str, str] = {}
        for c in node:
            name = _local(c.tag)
            if name == "link" and c.get("href"):
                f.setdefault("link", c.get("href") or "")
            elif c.text and name not in f:
                f[name] = c.text.strip()
        at = _when(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
        if at and f.get("title"):
            items.append(FeedItem(f["title"], f.get("link", ""), at))
    return items


def _hour(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def count_by_hour(
    items: Sequence[FeedItem], topics: Sequence[Topic], since: datetime
) -> list[SignalPoint]:
    buckets: dict[tuple[str, datetime], list[FeedItem]] = {}
    for it in items:
        if it.at < since:
            continue
        for t in topics:
            if t.matches(it.title):
                buckets.setdefault((t.id, _hour(it.at)), []).append(it)
    return [
        SignalPoint(
            at=hour,
            value=float(len(group)),
            topic_id=tid,
            attrs={"urls": [g.link for g in group[:3]]},
        )
        for (tid, hour), group in sorted(buckets.items(), key=lambda kv: kv[0][1])
    ]


async def _read_feeds(ctx: TrendsContext, feeds: Sequence[str]) -> list[FeedItem]:
    out: list[FeedItem] = []
    for url in feeds:
        try:
            out.extend(parse_feed(await ctx.get_text(url)))
        except (SignalUnavailable, CrawlError):
            continue  # one broken feed must not hide the others
    return out


class FeedVolumeSignal:
    """How many items per hour in the configured RSS/Atom feeds mention each topic."""

    name = "feed_volume"

    def __init__(self, feeds: Sequence[str]):
        self.feeds = list(feeds)

    async def collect(
        self, ctx: TrendsContext, topics: Sequence[Topic], since: datetime
    ) -> list[SignalPoint]:
        return count_by_hour(await _read_feeds(ctx, self.feeds), topics, since)


# -- emerging terms ------------------------------------------------------------------------------


def ngrams(title: str, max_n: int = 3) -> set[str]:
    words = [w.lower() for w in re.findall(r"[^\W_]+", title)]
    out = set()
    for n in range(1, max_n + 1):
        for i in range(len(words) - n + 1):
            gram = words[i : i + n]
            if (
                gram[0] in _STOP
                or gram[-1] in _STOP
                or any(len(w) < 3 for w in gram[:1] + gram[-1:])
            ):
                continue
            out.add(" ".join(gram))
    return out


class EmergingTermsSignal:
    """Phrases that suddenly recur in feed titles and match no topic. Each becomes a point with
    ``topic_id=None`` so the scorer can surface it as a new candidate label."""

    name = "emerging_terms"

    def __init__(self, feeds: Sequence[str], *, min_count: int = 3, max_terms: int = 20):
        self.feeds, self.min_count, self.max_terms = list(feeds), min_count, max_terms

    async def collect(
        self, ctx: TrendsContext, topics: Sequence[Topic], since: datetime
    ) -> list[SignalPoint]:
        items = [i for i in await _read_feeds(ctx, self.feeds) if i.at >= since]
        counts: Counter[str] = Counter()
        hours: dict[str, Counter[datetime]] = {}
        for it in items:
            if any(t.matches(it.title) for t in topics):
                continue
            for g in ngrams(it.title):
                counts[g] += 1
                hours.setdefault(g, Counter())[_hour(it.at)] += 1
        # Keep the longest phrase when a shorter one only appears inside it ("vegan" vs "vegan butter").
        terms = [t for t, c in counts.most_common() if c >= self.min_count]
        terms = [
            t for t in terms if not any(t != u and t in u and counts[u] >= counts[t] for u in terms)
        ]
        points = []
        for term in terms[: self.max_terms]:
            for hour, n in hours[term].items():
                points.append(SignalPoint(at=hour, value=float(n), label=term))
        return points


# -- GDELT -------------------------------------------------------------------------------------


def parse_gdelt_timeline(doc: Any, topic_id: str) -> list[SignalPoint]:
    """GDELT DOC API ``mode=timelinevol``: ``{"timeline": [{"data": [{"date": "20260101T000000Z", "value": 0.02}]}]}``
    where value is the share of monitored coverage. Not checked against the live API here."""
    points = []
    for series in doc.get("timeline", []):
        for row in series.get("data", []):
            try:
                at = datetime.strptime(row["date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                points.append(SignalPoint(at=at, value=float(row["value"]), topic_id=topic_id))
            except (KeyError, ValueError, TypeError):
                continue
    return points


class GdeltVolumeSignal:
    """Coverage volume per topic from GDELT (free, no key). One request per topic."""

    name = "gdelt_volume"
    URL = "https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=timelinevol&format=json&timespan={days}d"

    async def collect(
        self, ctx: TrendsContext, topics: Sequence[Topic], since: datetime
    ) -> list[SignalPoint]:
        days = max(1, min(30, (datetime.now(UTC) - since).days + 1))
        points: list[SignalPoint] = []
        for t in topics:
            if not t.keywords:
                continue
            query = "(" + " OR ".join(f'"{k}"' for k in t.keywords) + ")"
            try:
                doc = json.loads(await ctx.get_text(self.URL.format(q=quote(query), days=days)))
            except (SignalUnavailable, CrawlError, ValueError):
                continue
            points.extend(p for p in parse_gdelt_timeline(doc, t.id) if p.at >= since)
        return points


# -- search trends via a ladder ------------------------------------------------------------------

Rung = Callable[[TrendsContext, Sequence[Topic], datetime], Awaitable[list[SignalPoint]]]


def parse_trending_rss(xml: str | bytes, topics: Sequence[Topic], region: str) -> list[SignalPoint]:
    """A public "trending searches" RSS: ``<item><title>term</title><ht:approx_traffic>200+</ht:approx_traffic>
    <pubDate>..</pubDate></item>``. Traffic is the lower bound shown ("200+" is 200)."""
    data = xml.encode() if isinstance(xml, str) else xml
    if _DTD.search(data):
        raise SignalUnavailable("feed contains a DTD; refusing to parse")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise SignalUnavailable(f"invalid trends page: {e}") from e
    points = []
    for item in root.iter("item"):
        f = {_local(c.tag): (c.text or "").strip() for c in item}
        at = _when(f.get("pubDate"))
        traffic = re.sub(r"[^\d]", "", f.get("approx_traffic", ""))
        if not (at and f.get("title") and traffic):
            continue
        for t in topics:
            if t.matches(f["title"]):
                points.append(
                    SignalPoint(
                        at=_hour(at),
                        value=float(traffic),
                        topic_id=t.id,
                        region=region,
                        attrs={"query": f["title"]},
                    )
                )
    return points


def scrape_rung(url_for_region: Callable[[str], str], regions: Sequence[str]) -> Rung:
    """Rung that reads a public trending page through ``tsp.crawl`` (robots, limits, SSRF guard)."""

    async def rung(
        ctx: TrendsContext, topics: Sequence[Topic], since: datetime
    ) -> list[SignalPoint]:
        out: list[SignalPoint] = []
        for region in regions:
            try:
                out.extend(
                    p
                    for p in parse_trending_rss(
                        await ctx.get_text(url_for_region(region)), topics, region
                    )
                    if p.at >= since
                )
            except CrawlError as e:
                raise SignalUnavailable(str(e)) from e
        return out

    return rung


class SearchTrendsSignal:
    """Search interest, cheapest method first: ``rungs`` are tried in order and the first that answers
    wins, as in the K1 ladder (public page, then a free API, then a paid one)."""

    name = "search_trends"

    def __init__(self, rungs: Sequence[tuple[str, Rung]]):
        self.rungs = list(rungs)
        self.last_rung: str | None = None

    async def collect(
        self, ctx: TrendsContext, topics: Sequence[Topic], since: datetime
    ) -> list[SignalPoint]:
        errors = []
        for name, rung in self.rungs:
            try:
                points = await rung(ctx, topics, since)
            except (SignalUnavailable, CrawlError) as e:
                errors.append(f"{name}: {e}")
                continue
            self.last_rung = name
            return points
        raise SignalUnavailable("; ".join(errors) or "no rungs configured")


# -- a K1 source as a signal ---------------------------------------------------------------------


class CustomFeedSignal:
    """Counts items a ``tsp.sources`` source stream stored per hour, mapped to topics by keyword in
    the item's title or by ``classify(title, metadata) -> topic keys`` when given."""

    def __init__(
        self,
        source_key: str,
        stream: str,
        *,
        name: str | None = None,
        classify: Callable[[str, dict[str, Any]], Sequence[str]] | None = None,
    ):
        self.source_key, self.stream, self.classify = source_key, stream, classify
        self.name = name or f"source:{source_key}"

    async def collect(
        self, ctx: TrendsContext, topics: Sequence[Topic], since: datetime
    ) -> list[SignalPoint]:
        async with ctx.db.session() as s:
            rows = (
                await s.execute(
                    select(SourceItem.fetched_at, SourceItem.url, SourceItem.meta)
                    .join(Source, Source.id == SourceItem.source_id)
                    .where(
                        Source.key == self.source_key,
                        SourceItem.stream == self.stream,
                        SourceItem.fetched_at >= since,
                    )
                )
            ).all()
        by_key = {t.key: t for t in topics}
        buckets: dict[tuple[str, datetime], list[str]] = {}
        for at, url, meta in rows:
            title = str((meta or {}).get("title") or "")
            hit = (
                [by_key[k] for k in self.classify(title, meta or {}) if k in by_key]
                if self.classify
                else [t for t in topics if t.matches(title)]
            )
            for t in hit:
                buckets.setdefault((t.id, _hour(at)), []).append(url or "")
        return [
            SignalPoint(at=hour, value=float(len(urls)), topic_id=tid, attrs={"urls": urls[:3]})
            for (tid, hour), urls in sorted(buckets.items(), key=lambda kv: kv[0][1])
        ]


def window_start(now: datetime, days: int = 1) -> datetime:
    return now - timedelta(days=days)
