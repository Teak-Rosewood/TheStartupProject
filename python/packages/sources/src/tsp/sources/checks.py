from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import select
from tsp.audit import Actor
from tsp.audit import record as audit
from tsp.cache import Cache
from tsp.crawl import CrawlError, HttpFetcher, NotModified, RobotsCache
from tsp.db import Database

from .models import Check, Source
from .runner import make_robots

SYSTEM = Actor(None, "system")


@dataclass
class CheckResult:
    source_key: str
    reachable: bool
    robots_ok: bool | None  # None: robots.txt could not be read
    terms_hash: str | None
    disabled: bool
    reasons: list[str]


class SourceChecks:
    """Health and policy check for one source: reachability, robots.txt, and a hash of the terms page.

    Policy changes switch the source off and write an audit entry; an unreadable robots.txt or an
    unreachable site is only recorded, because outages must not disable sources."""

    def __init__(
        self, db: Database, http: HttpFetcher, cache: Cache, robots: RobotsCache | None = None
    ):
        self.db, self.http = db, http
        self.robots = robots or make_robots(http, cache)

    async def check(self, source_key: str) -> CheckResult:
        async with self.db.session() as s:
            src = (
                await s.execute(select(Source).where(Source.key == source_key))
            ).scalar_one_or_none()
            if src is None:
                raise KeyError(f"unknown source {source_key!r}")
            previous = (
                await s.execute(
                    select(Check.terms_hash)
                    .where(Check.source_id == src.id, Check.terms_hash.is_not(None))
                    .order_by(Check.checked_at.desc(), Check.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            url, terms_url, was_robots_ok = src.url, src.terms_url, src.robots_ok

        notes: list[str] = []
        try:
            reachable = (await self.http.fetch_raw(url)).status < 500
        except CrawlError as e:
            reachable, notes = False, [f"unreachable: {e}"]
        await self.robots.invalidate(url)
        robots_ok = await self.robots.verdict(url, self.http.user_agent)
        terms_hash = None
        if terms_url:
            try:
                page = await self.http.fetch(terms_url)
                terms_hash = None if isinstance(page, NotModified) else page.content_hash
            except CrawlError as e:
                notes.append(f"terms page unreadable: {e}")

        reasons = []
        if robots_ok is False and was_robots_ok:
            reasons.append("robots.txt now disallows our crawler")
        if terms_hash and previous and terms_hash != previous:
            reasons.append("terms page changed")

        async with self.db.session() as s:
            row = (await s.execute(select(Source).where(Source.key == source_key))).scalar_one()
            s.add(
                Check(
                    source_id=row.id,
                    robots_ok=robots_ok,
                    terms_hash=terms_hash,
                    reachable=reachable,
                    notes="; ".join(notes + reasons) or None,
                )
            )
            if robots_ok is not None:
                row.robots_ok = robots_ok
            if terms_hash:
                from datetime import datetime

                row.terms_checked_at = datetime.now(UTC)
            disabled = bool(reasons) and row.enabled
            if disabled:
                row.enabled = False
                await audit(
                    s,
                    "sources.disabled",
                    actor=SYSTEM,
                    target=("source", source_key),
                    reasons=reasons,
                )
        return CheckResult(source_key, reachable, robots_ok, terms_hash, disabled, reasons)
