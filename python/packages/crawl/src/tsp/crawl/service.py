from __future__ import annotations

import fnmatch
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record
from tsp.cache import Cache
from tsp.db import Database
from tsp.jobs import claim_jobs, enqueue, fail_job, finish_job
from tsp.metering import UsageRecord, record_usage
from tsp.vector import BadRequest, LimitExceeded, NotFound, VectorService

from .chain import needs_render
from .config import MAX_PAGES_HARD_CAP, MAX_PATTERNS, CrawlSettings
from .errors import CrawlError, FetchError, RateLimited, UnsafeURL
from .fetch_http import HttpFetcher
from .models import CrawlPage, CrawlSource
from .page import Fetcher, NotModified, Page, content_hash
from .politeness import DomainLimiter
from .render_api import RenderResult
from .robots import RobotsCache
from .sitemap import parse_sitemap
from .urls import normalise

PLAN_JOB, FETCH_JOB, REFRESH_JOB, PURGE_JOB = (
    "crawl.plan",
    "crawl.fetch",
    "crawl.refresh",
    "crawl.purge",
)
SYSTEM = Actor(None, "system")
PURGE_BATCH = 100
RENDER_JOB = RENDER_QUEUE = "crawl.render"  # job name and queue: only the render worker takes them
Limits = Callable[[AsyncSession, str], Awaitable[dict[str, Any]]]


def _slot(now: datetime, seconds: int) -> int:
    """Job keys carry a time slot so a later crawl is not mistaken for a repeat of an earlier one."""
    return int(now.timestamp() // seconds)


def in_scope(seed_url: str, include: list[str], exclude: list[str], url: str) -> bool:
    p, seed = urlsplit(url), urlsplit(seed_url)
    if p.scheme not in ("http", "https") or p.hostname != seed.hostname:
        return False
    if include and not any(fnmatch.fnmatchcase(url, g) for g in include):
        return False
    return not any(fnmatch.fnmatchcase(url, g) for g in exclude)


def _patterns(name: str, value: list[str] | None) -> list[str]:
    value = list(value or [])
    if len(value) > MAX_PATTERNS or any(
        not isinstance(v, str) or not v or len(v) > 300 for v in value
    ):
        raise BadRequest(f"{name}: up to {MAX_PATTERNS} patterns of 1-300 characters")
    return value


@dataclass
class _Job:
    """What a fetch needs, copied out so the database transaction is closed during network I/O."""

    page_id: str
    org_id: str
    source_id: str
    seed_url: str
    include: list[str]
    exclude: list[str]
    max_depth: int
    max_pages: int
    render: str
    created_by: str | None
    collection_id: str
    url: str
    depth: int
    etag: str | None
    last_modified: str | None
    content_hash: str | None
    vector_document_id: str | None


@dataclass
class CrawlService:
    """Sources, pages and the plan, fetch and purge steps that jobs call.

    ``fetcher`` does the plain HTTP fetch for every source. A source with ``render`` of ``always``
    (or ``auto`` when the plain result is thin or JavaScript-only) is rendered in a browser:
    with ``render_remote`` the page is queued on ``crawl.render`` for the render worker, which
    reports back through the internal render API; otherwise ``render_fetcher``, if given, renders
    in this process. With neither, an ``always`` page fails with a clear error instead of being
    fetched unrendered."""

    vector: VectorService
    fetcher: Fetcher
    http: HttpFetcher
    robots: RobotsCache
    cache: Cache
    db: Database | None = None
    settings: CrawlSettings = field(default_factory=CrawlSettings)
    limits: Limits | None = None
    render_fetcher: Fetcher | None = None
    render_remote: bool = (
        False  # pages needing a browser go to the render queue for the render worker
    )

    def bind(self, db: Database) -> None:
        self.db = db

    def _session(self) -> Any:
        if self.db is None:
            raise RuntimeError("CrawlService has no database; pass db= or call bind(db)")
        return self.db.session()

    # -- sources -----------------------------------------------------------------------------

    async def create_source(
        self,
        session: AsyncSession,
        org_id: str,
        *,
        collection: str,
        seed_url: str,
        sitemap_url: str | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        max_depth: int = 2,
        max_pages: int = 500,
        render: str = "never",
        refresh_hours: int = 168,
        user_sub: str | None = None,
        actor: Actor,
        request: Any = None,
    ) -> CrawlSource:
        if render not in ("never", "auto", "always"):
            raise BadRequest("render must be never, auto or always")
        if not (0 <= max_depth <= 10) or not (1 <= max_pages <= MAX_PAGES_HARD_CAP):
            raise BadRequest(f"max_depth 0-10 and max_pages 1-{MAX_PAGES_HARD_CAP}")
        seed_url = normalise(seed_url)
        for label, url in (("seed_url", seed_url), ("sitemap_url", sitemap_url)):
            if url is None:
                continue
            try:
                await self.http.check_url(url)
            except UnsafeURL as e:
                raise BadRequest(f"{label}: {e}") from e
        if sitemap_url and urlsplit(sitemap_url).hostname != urlsplit(seed_url).hostname:
            raise BadRequest("sitemap_url must be on the same host as seed_url")
        caps = await self.limits(session, org_id) if self.limits else {}
        if (cap := caps.get("crawl_depth")) is not None and max_depth > cap:
            raise LimitExceeded(f"your plan allows a crawl depth of {cap}")
        if (floor := caps.get("crawl_refresh_min_hours")) is not None and refresh_hours < floor:
            raise LimitExceeded(f"your plan refreshes at most every {floor} hours")
        if refresh_hours < 1:
            raise BadRequest("refresh_hours must be at least 1")
        col = await self.vector.collection(session, org_id, collection)
        src = CrawlSource(
            org_id=org_id,
            collection_id=col.id,
            seed_url=seed_url,
            sitemap_url=normalise(sitemap_url) if sitemap_url else None,
            include=_patterns("include", include),
            exclude=_patterns("exclude", exclude),
            max_depth=max_depth,
            max_pages=max_pages,
            render=render,
            refresh_hours=refresh_hours,
            created_by=user_sub,
        )
        session.add(src)
        await session.flush()
        await record(
            session,
            "crawl.source_created",
            actor=actor,
            target=("crawl_source", src.id),
            request=request,
            seed_url=seed_url,
        )
        await self._enqueue_plan(session, src.id, "first")
        return src

    async def source(self, session: AsyncSession, org_id: str, source_id: str) -> CrawlSource:
        src = await session.get(CrawlSource, source_id)
        if src is None or src.org_id != org_id or src.status == "deleting":
            raise NotFound("no such source")
        return src

    async def sources(self, session: AsyncSession, org_id: str) -> list[CrawlSource]:
        q = select(CrawlSource).where(
            CrawlSource.org_id == org_id, CrawlSource.status != "deleting"
        )
        return list((await session.execute(q.order_by(CrawlSource.created_at))).scalars())

    async def pages(
        self,
        session: AsyncSession,
        org_id: str,
        source_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CrawlPage]:
        await self.source(session, org_id, source_id)
        q = select(CrawlPage).where(CrawlPage.source_id == source_id)
        if status:
            q = q.where(CrawlPage.status == status)
        q = q.order_by(CrawlPage.url).limit(max(1, min(limit, 500))).offset(max(0, offset))
        return list((await session.execute(q)).scalars())

    async def set_status(
        self, session: AsyncSession, org_id: str, source_id: str, status: str, *, actor: Actor
    ) -> CrawlSource:
        if status not in ("active", "paused"):
            raise BadRequest("status must be active or paused")
        src = await self.source(session, org_id, source_id)
        src.status = status
        await record(
            session, f"crawl.source_{status}", actor=actor, target=("crawl_source", source_id)
        )
        return src

    async def recrawl(self, session: AsyncSession, org_id: str, source_id: str) -> None:
        src = await self.source(session, org_id, source_id)
        if src.status != "active":
            raise BadRequest("resume the source before recrawling")
        await self._enqueue_plan(session, source_id, f"manual-{_slot(datetime.now(UTC), 60)}")

    async def delete_source(
        self,
        session: AsyncSession,
        org_id: str,
        source_id: str,
        *,
        actor: Actor,
        request: Any = None,
    ) -> None:
        """Hide the source now; ``crawl.purge`` removes its pages and indexed documents in batches."""
        src = await self.source(session, org_id, source_id)
        src.status = "deleting"
        await record(
            session,
            "crawl.source_deleted",
            actor=actor,
            target=("crawl_source", source_id),
            request=request,
        )
        await enqueue(
            session, PURGE_JOB, {"source_id": source_id}, unique_key=f"crawl.purge:{source_id}"
        )

    async def _enqueue_plan(self, session: AsyncSession, source_id: str, slot: str) -> None:
        await enqueue(
            session, PLAN_JOB, {"source_id": source_id}, unique_key=f"crawl.plan:{source_id}:{slot}"
        )

    # -- jobs --------------------------------------------------------------------------------

    async def refresh_due(self) -> int:
        """Queue a plan for each active source whose refresh interval has passed."""
        now = datetime.now(UTC)
        n = 0
        async with self._session() as s:
            rows = (
                await s.execute(select(CrawlSource).where(CrawlSource.status == "active"))
            ).scalars()
            for src in rows:
                due = (
                    src.last_run_at is None
                    or (now - src.last_run_at).total_seconds() >= src.refresh_hours * 3600
                )
                if due:
                    await self._enqueue_plan(s, src.id, f"refresh-{_slot(now, 3600)}")
                    n += 1
        return n

    async def _sitemap_urls(self, src: CrawlSource) -> list[str]:
        out: list[str] = []
        queue, seen = [(src.sitemap_url, 0)] if src.sitemap_url else [], set()
        while queue and len(out) < src.max_pages:
            url, depth = queue.pop(0)
            if url in seen or not in_scope(src.seed_url, [], [], url):
                continue
            seen.add(url)
            try:
                raw = await self.http.fetch_raw(url)
                if raw.status >= 400:
                    continue
                for e in parse_sitemap(raw.body):
                    if e.is_sitemap:
                        if depth < 2:
                            queue.append((e.loc, depth + 1))
                    else:
                        out.append(normalise(e.loc))
            except CrawlError:
                continue  # a broken sitemap must not stop the crawl from the seed
        return out

    async def plan(self, source_id: str) -> int:
        """Expand a source into pages and queue a fetch for each. Safe to run twice."""
        now = datetime.now(UTC)
        async with self._session() as s:
            src = await s.get(CrawlSource, source_id)
            if src is None or src.status != "active":
                return 0
            candidates = [src.seed_url, *await self._sitemap_urls(src)]
            urls = [
                u
                for u in dict.fromkeys(candidates)
                if in_scope(src.seed_url, src.include, src.exclude, u)
            ]
            have = (
                await s.execute(
                    select(func.count()).select_from(CrawlPage).where(CrawlPage.source_id == src.id)
                )
            ).scalar_one()
            room = max(0, src.max_pages - have)
            if urls:
                existing = set(
                    (
                        await s.execute(
                            select(CrawlPage.url).where(
                                CrawlPage.source_id == src.id, CrawlPage.url.in_(urls)
                            )
                        )
                    ).scalars()
                )
                new = [u for u in urls if u not in existing][:room]
                if new:
                    await s.execute(
                        insert(CrawlPage)
                        .values(
                            [
                                {
                                    "id": _new_id(),
                                    "org_id": src.org_id,
                                    "source_id": src.id,
                                    "url": u,
                                    "depth": 0,
                                }
                                for u in new
                            ]
                        )
                        .on_conflict_do_nothing(constraint="uq_crawl_pages_source_url")
                    )
            src.last_run_at = now
            ids = (
                (
                    await s.execute(
                        select(CrawlPage.id).where(
                            CrawlPage.source_id == src.id, CrawlPage.status != "gone"
                        )
                    )
                )
                .scalars()
                .all()
            )
            for pid in ids:
                await self._enqueue_fetch(s, src.id, pid, now, render=src.render == "always")
            return len(ids)

    async def _enqueue_fetch(
        self, s: AsyncSession, source_id: str, page_id: str, now: datetime, *, render: bool = False
    ) -> None:
        key = f"crawl:{source_id}:{_slot(now, 60)}:{page_id}"
        if render and self.render_remote:
            await enqueue(
                s, RENDER_JOB, {"page_id": page_id}, unique_key=key + ":r", queue=RENDER_QUEUE
            )
        else:
            await enqueue(s, FETCH_JOB, {"page_id": page_id}, unique_key=key)

    async def _pages_this_month(self, s: AsyncSession, org_id: str) -> int:
        month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(UsageRecord)
                    .where(
                        UsageRecord.org_id == org_id,
                        UsageRecord.kind == "crawl.page",
                        UsageRecord.ts >= month,
                    )
                )
            ).scalar_one()
        )

    async def prepare(self, page_id: str) -> _Job | None:
        """Load what a fetch needs. ``None`` when there is nothing to do: the page or source is gone
        or paused, or the organization has used its monthly pages (the page is marked then)."""
        async with self._session() as s:
            row = (
                await s.execute(
                    select(CrawlPage, CrawlSource)
                    .join(CrawlSource, CrawlSource.id == CrawlPage.source_id)
                    .where(CrawlPage.id == page_id)
                )
            ).first()
            if row is None or row[1].status != "active":
                return None
            page, src = row
            caps = await self.limits(s, src.org_id) if self.limits else {}
            cap = caps.get("crawl_pages_month")  # soft under concurrency: see docs
            if cap is not None and await self._pages_this_month(s, src.org_id) >= cap:
                page.status, page.error = "error", "monthly page limit reached"
                return None
            return _Job(
                page.id, src.org_id, src.id, src.seed_url, list(src.include), list(src.exclude),
                src.max_depth, src.max_pages, src.render, src.created_by, src.collection_id,
                page.url, page.depth, page.etag, page.last_modified, page.content_hash,
                page.vector_document_id,
            )  # fmt: skip

    async def fetch_page(self, page_id: str) -> None:
        """Fetch one page and apply the result. Raises for transient failures so the job retries."""
        job = await self.prepare(page_id)
        if job is None:
            return
        outcome = await self._fetch(job)
        async with self._session() as s:
            transient = await self._apply(s, job, outcome)
        if transient:
            raise RuntimeError(transient)

    async def _fetch(self, job: _Job) -> tuple[str, Any]:
        host, ua = urlsplit(job.url).netloc, self.settings.user_agent
        fetcher = self.fetcher
        if job.render == "always":
            if self.render_remote:
                return "defer", None  # belongs on the render queue (it was queued here by mistake)
            if self.render_fetcher is None:
                return "error", ("rendering is not configured", None, False)
            fetcher = self.render_fetcher
        limiter = DomainLimiter(self.cache, self.settings.rps)
        try:
            if not await self.robots.allowed(job.url, ua):
                return "robots", None
            await limiter.acquire(
                host,
                delay=await self.robots.crawl_delay(job.url, ua),
                max_wait=self.settings.max_wait_seconds,
            )
            result = await fetcher.fetch(job.url, etag=job.etag, last_modified=job.last_modified)
        except UnsafeURL as e:
            return "error", (f"blocked: {e}", None, False)
        except RateLimited as e:
            return "error", (str(e), 429, True)
        except FetchError as e:
            if e.status in (404, 410):
                return "gone", e.status
            if e.status is not None:
                await limiter.report(host, e.status)
            transient = e.status is None or e.status == 429 or e.status >= 500
            return "error", (str(e), e.status, transient)
        except CrawlError as e:
            return "error", (str(e), None, False)
        await limiter.report(host, 200 if isinstance(result, NotModified) else result.status)
        if isinstance(result, NotModified):
            return "not_modified", result
        if job.render == "auto" and self.render_remote and needs_render(result):
            return "defer", None  # thin or JavaScript-only: a browser should read it
        return "page", result

    async def _apply(self, s: AsyncSession, job: _Job, outcome: tuple[str, Any]) -> str | None:
        """Write the fetch result. Returns an error message when the job should be retried."""
        page = await s.get(CrawlPage, job.page_id)
        if page is None:
            return None  # the source was purged while we were fetching
        kind, data = outcome
        now = datetime.now(UTC)
        if kind == "defer":
            await enqueue(
                s,
                RENDER_JOB,
                {"page_id": page.id},
                unique_key=f"crawl:{job.source_id}:{_slot(now, 60)}:{page.id}:r",
                queue=RENDER_QUEUE,
            )
            return None
        page.fetched_at = now
        if kind == "robots":
            page.status, page.error = "skipped_robots", None
        elif kind == "gone":
            page.status, page.http_status, page.error = "gone", data, None
            await self._release_document(s, page)
        elif kind == "error":
            message, status, transient = data
            page.status, page.error, page.http_status = "error", message[:500], status
            return message if transient else None
        elif kind == "not_modified":
            page.status, page.error = "not_modified", None
        else:
            await self._store_page(s, job, page, data, now)
        return None

    async def _store_page(
        self, s: AsyncSession, job: _Job, page: CrawlPage, got: Any, now: datetime
    ) -> None:
        page.final_url, page.etag, page.last_modified = got.final_url, got.etag, got.last_modified
        page.http_status, page.error = got.status, None
        await record_usage(
            s, org_id=job.org_id, kind="crawl.page", units_in=1, source_id=job.source_id
        )
        if not got.markdown.strip():
            page.status, page.error = "error", "no extractable text"
            return
        page.status = "ok"
        if got.content_hash != job.content_hash or job.vector_document_id is None:
            previous = page.vector_document_id
            doc = await self.vector.add_document(
                s,
                job.org_id,
                await self._collection_name(s, job.collection_id),
                text_=got.markdown,
                source_uri=job.url,
                title=got.title or "",
                user_sub=job.created_by,
                metadata={"fetched_at": now.isoformat(), "title": got.title, "url": job.url},
            )
            page.content_hash, page.vector_document_id = got.content_hash, doc.id
            if previous and previous != doc.id:
                await self._drop_document(s, job.org_id, page.id, previous)
        await self._follow_links(s, job, got.links, now)

    async def _collection_name(self, s: AsyncSession, collection_id: str) -> str:
        from tsp.vector import Collection

        col = await s.get(Collection, collection_id)
        if col is None:
            raise NotFound("the source's collection no longer exists")
        return col.name

    async def _follow_links(
        self, s: AsyncSession, job: _Job, links: list[str], now: datetime
    ) -> None:
        if job.depth >= job.max_depth:
            return
        total = (
            await s.execute(
                select(func.count())
                .select_from(CrawlPage)
                .where(CrawlPage.source_id == job.source_id)
            )
        ).scalar_one()
        room = job.max_pages - total
        fresh = [
            u for u in dict.fromkeys(links) if in_scope(job.seed_url, job.include, job.exclude, u)
        ]
        if room <= 0 or not fresh:
            return
        rows = [
            {
                "id": _new_id(),
                "org_id": job.org_id,
                "source_id": job.source_id,
                "url": u,
                "depth": job.depth + 1,
            }
            for u in fresh[:room]
        ]
        added = (
            await s.execute(
                insert(CrawlPage)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_crawl_pages_source_url")
                .returning(CrawlPage.id)
            )
        ).scalars()
        for pid in added:
            await self._enqueue_fetch(s, job.source_id, pid, now, render=job.render == "always")

    async def _drop_document(self, s: AsyncSession, org_id: str, page_id: str, doc_id: str) -> None:
        """Delete a vector document unless another page of ours still points at it."""
        others = (
            await s.execute(
                select(func.count())
                .select_from(CrawlPage)
                .where(CrawlPage.vector_document_id == doc_id, CrawlPage.id != page_id)
            )
        ).scalar_one()
        if not others:
            try:
                await self.vector.delete_document(s, org_id, doc_id, actor=SYSTEM)
            except NotFound:
                pass

    async def _release_document(self, s: AsyncSession, page: CrawlPage) -> None:
        if page.vector_document_id:
            await self._drop_document(s, page.org_id, page.id, page.vector_document_id)
        page.vector_document_id = page.content_hash = None

    # -- the render worker's side ----------------------------------------------------------

    async def claim_render(
        self, worker_id: str, lease: timedelta = timedelta(minutes=10)
    ) -> dict[str, Any] | None:
        """Hand the render worker its next page, or ``None`` when the queue is empty. robots.txt and
        the per-host limiter are applied here, so the browser never sees a page we may not fetch."""
        db = self._need_db()
        for _ in range(25):  # skip jobs with nothing to do instead of answering "empty" for each
            claimed = await claim_jobs(db, [RENDER_QUEUE], worker_id, lease, 1)
            if not claimed:
                return None
            job = claimed[0]
            prepared = await self.prepare(job["payload"]["page_id"])
            if prepared is None:
                await finish_job(db, job["id"])
                continue
            ua = self.settings.user_agent
            host = urlsplit(prepared.url).netloc
            try:
                if not await self.robots.allowed(prepared.url, ua):
                    async with self._session() as s:
                        await self._apply(s, prepared, ("robots", None))
                    await finish_job(db, job["id"])
                    continue
                await DomainLimiter(self.cache, self.settings.rps).acquire(
                    host,
                    delay=await self.robots.crawl_delay(prepared.url, ua),
                    max_wait=self.settings.max_wait_seconds,
                )
            except RateLimited as e:
                await fail_job(db, job, str(e))
                continue
            return {"job_id": job["id"], "url": prepared.url, "user_agent": ua}
        return None

    async def submit_render(self, job_id: int, result: RenderResult) -> None:
        """Apply what the render worker found. The job must be a running ``crawl.render`` job."""
        db = self._need_db()
        async with self._session() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT id, payload, attempts, max_attempts FROM jobs "
                        "WHERE id = :id AND queue = :q AND status = 'running'"
                    ),
                    {"id": job_id, "q": RENDER_QUEUE},
                )
            ).first()
        if row is None:
            raise NotFound("no such render job")
        job = dict(row._mapping)
        prepared = await self.prepare(job["payload"]["page_id"])
        if prepared is None:
            await finish_job(db, job_id)
            return
        outcome: tuple[str, Any]
        if result.error is not None:
            outcome = ("error", (result.error, result.status, result.transient))
        else:
            try:
                await self.http.check_url(result.final_url)  # the browser may have been redirected
            except UnsafeURL as e:
                outcome = ("error", (f"blocked: {e}", None, False))
            else:
                outcome = (
                    "page",
                    Page(
                        url=prepared.url, final_url=result.final_url, status=result.status or 200,
                        title=result.title, markdown=result.markdown, links=result.links,
                        content_hash=content_hash(result.markdown), etag=None, last_modified=None,
                        fetched_at=datetime.now(UTC), content_type="text/html",
                        fetcher="browser",
                    ),
                )  # fmt: skip
        async with self._session() as s:
            transient = await self._apply(s, prepared, outcome)
        if transient:
            await fail_job(db, job, transient)
        else:
            await finish_job(db, job_id)

    def _need_db(self) -> Database:
        if self.db is None:
            raise RuntimeError("CrawlService has no database; pass db= or call bind(db)")
        return self.db

    async def purge(self, source_id: str) -> None:
        """Delete a source's documents in batches, then the source and its pages."""
        while True:
            async with self._session() as s:
                src = await s.get(CrawlSource, source_id)
                if src is None or src.status != "deleting":
                    return
                batch = (
                    (
                        await s.execute(
                            select(CrawlPage)
                            .where(
                                CrawlPage.source_id == source_id,
                                CrawlPage.vector_document_id.is_not(None),
                            )
                            .limit(PURGE_BATCH)
                        )
                    )
                    .scalars()
                    .all()
                )
                for page in batch:
                    await self._release_document(s, page)
                if not batch:
                    await s.delete(src)
                    return


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex


__all__ = ["CrawlService", "in_scope"]
