from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from tsp.cache import Cache
from tsp.crawl import ChainFetcher, DomainLimiter, Fetcher, HttpFetcher, RobotsCache
from tsp.db import Database
from tsp.metering import record_usage

from .config import SourcesConfig
from .connector import (
    Connector,
    ConnectorContext,
    Item,
    PaidBudget,
    SourceInfo,
    Strategy,
    StrategyFailed,
)
from .models import Cursor, Run, Source
from .models import Item as ItemRow
from .registry import get_connector
from .store import RawStore


class SourceDisabled(Exception):
    pass


@dataclass
class StreamResult:
    stream: str
    strategy_used: str | None
    status: str
    items_new: int = 0
    items_updated: int = 0
    errors: int = 0
    cost: Decimal = Decimal(0)
    run_id: int | None = None
    error_summary: str | None = None


@dataclass
class RunResult:
    streams: list[StreamResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        states = {s.status for s in self.streams}
        return "failed" if states == {"failed"} else "ok" if states <= {"ok"} else "partial"

    @property
    def items_new(self) -> int:
        return sum(s.items_new for s in self.streams)

    @property
    def items_updated(self) -> int:
        return sum(s.items_updated for s in self.streams)


@dataclass
class ReconcileReport:
    stream: str
    strategies: tuple[str, str]
    compared: int
    only_in_first: list[str]
    only_in_second: list[str]
    different: list[str]

    @property
    def consistent(self) -> bool:
        return not (self.only_in_first or self.only_in_second or self.different)


@dataclass
class _State:
    cursor: str | None
    new: int = 0
    updated: int = 0
    progress_by: str | None = None
    manifest: list[dict[str, Any]] = field(default_factory=list)


def source_info(row: Source) -> SourceInfo:
    return SourceInfo(
        id=row.id,
        key=row.key,
        url=row.url,
        org_id=row.org_id,
        robots_ok=row.robots_ok,
        allowed_uses=tuple(row.allowed_uses),
        strategies=tuple(row.strategies),
        rate_limit_rps=row.rate_limit_rps,
        paid_cap_monthly=row.paid_cap_monthly,
    )


def make_robots(http: HttpFetcher, cache: Cache) -> RobotsCache:
    async def fetch_text(url: str) -> tuple[int, str]:
        raw = await http.fetch_raw(url)
        return raw.status, raw.body.decode("utf-8", errors="replace")

    return RobotsCache(cache, fetch_text)


def _raw_key(prefix: str, source_key: str, item: Item, when: datetime) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", item.external_id).strip("._") or "item"
    if slug != item.external_id or len(slug) > 120:
        slug = slug[:100] + "-" + hashlib.sha1(item.external_id.encode()).hexdigest()[:8]
    ext = {"text/markdown": ".md"}.get(item.content_type) or (
        mimetypes.guess_extension(item.content_type) or ".bin"
    )
    year = (item.updated_at or when).year
    return f"{prefix}/{source_key}/{year}/{slug}{ext}"


class SourceRunner:
    """Runs a connector's strategies in the source row's order, with fallback and a resumable cursor.

    The cursor advances only after a batch of items has been stored and committed, so a crash
    repeats at most one batch (items are upserted by external id, so repeats are harmless). A
    fallback strategy starts from the cursor the failed one left, so cursors must mean the same
    thing across a connector's strategies."""

    def __init__(
        self,
        db: Database,
        store: RawStore,
        cache: Cache,
        http: HttpFetcher,
        *,
        fetcher: Fetcher | None = None,
        robots: RobotsCache | None = None,
        config: Mapping[str, str] | None = None,
        options: SourcesConfig | None = None,
    ):
        self.db, self.store, self.cache, self.http = db, store, cache, http
        self.fetcher = fetcher or ChainFetcher(http)
        self.robots = robots or make_robots(http, cache)
        self.config = dict(config or {})
        self.opts = options or SourcesConfig()

    async def _load(self, key: str) -> tuple[SourceInfo, Connector]:
        async with self.db.session() as s:
            row = (await s.execute(select(Source).where(Source.key == key))).scalar_one_or_none()
        if row is None:
            raise KeyError(f"unknown source {key!r}")
        if not row.enabled:
            raise SourceDisabled(key)
        return source_info(row), get_connector(key)()

    async def _context(self, src: SourceInfo) -> ConnectorContext:
        month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        async with self.db.session() as s:
            spent = (
                await s.execute(
                    select(func.coalesce(func.sum(Run.cost), 0)).where(
                        Run.source_id == src.id, Run.started_at >= month
                    )
                )
            ).scalar_one()
        return ConnectorContext(
            source=src,
            http=self.http,
            fetcher=self.fetcher,
            robots=self.robots,
            limiter=DomainLimiter(self.cache, src.rate_limit_rps),
            config=self.config,
            budget=PaidBudget(src.paid_cap_monthly, Decimal(spent)),
            options=self.opts,
        )

    async def sync(
        self,
        source_key: str,
        stream: str | None = None,
        since: str | None = None,
        *,
        strategy: str | None = None,
        dry_run: bool = False,
    ) -> RunResult:
        src, connector = await self._load(source_key)
        result = RunResult()
        for name in [stream] if stream else connector.streams:
            result.streams.append(
                await self._sync_stream(src, connector, name, since, strategy, dry_run)
            )
        return result

    async def _sync_stream(
        self,
        src: SourceInfo,
        connector: Connector,
        stream: str,
        since: str | None,
        only: str | None,
        dry_run: bool,
    ) -> StreamResult:
        started = datetime.now(UTC)
        ctx = await self._context(src)
        impls = connector.strategies()
        state = _State(since if since is not None else await self._cursor(src.id, stream))
        errors: list[str] = []
        completed_by: str | None = None
        crash: BaseException | None = None
        try:
            for name in [only] if only else list(src.strategies):
                strat = impls.get(name)
                if strat is None:
                    errors.append(f"{name}: connector has no such strategy")
                    continue
                if not await strat.available(ctx):
                    errors.append(f"{name}: unavailable")
                    continue
                try:
                    await self._consume(strat, name, ctx, src, connector, stream, state, dry_run)
                    completed_by = name
                    break
                except StrategyFailed as e:
                    errors.append(f"{name}: {e}")
        except Exception as e:
            crash = e
            errors.append(f"crashed: {type(e).__name__}: {e}")
        status = (
            "ok"
            if completed_by
            else "partial"
            if state.progress_by or state.new or state.updated
            else "failed"
        )
        out = StreamResult(
            stream=stream,
            strategy_used=completed_by or state.progress_by,
            status=status,
            items_new=state.new,
            items_updated=state.updated,
            errors=len(errors),
            cost=ctx.budget.spent_run,
            error_summary="; ".join(errors)[:2000] or None,
        )
        if not dry_run:
            out.run_id = await self._finish(src, stream, started, state, out)
        if crash is not None:
            raise crash
        return out

    async def _cursor(self, source_id: int, stream: str) -> str | None:
        async with self.db.session() as s:
            row = await s.get(Cursor, (source_id, stream))
            return row.value if row else None

    async def _consume(
        self,
        strat: Strategy,
        name: str,
        ctx: ConnectorContext,
        src: SourceInfo,
        connector: Connector,
        stream: str,
        state: _State,
        dry_run: bool,
    ) -> None:
        batch: list[Item] = []
        async for item in strat.fetch(ctx, stream, state.cursor):
            batch.append(item)
            if len(batch) >= self.opts.batch_size:
                await self._commit(batch, name, src, connector, stream, state, dry_run)
                batch = []
        if batch:
            await self._commit(batch, name, src, connector, stream, state, dry_run)

    async def _commit(
        self,
        batch: list[Item],
        name: str,
        src: SourceInfo,
        connector: Connector,
        stream: str,
        state: _State,
        dry_run: bool,
    ) -> None:
        now = datetime.now(UTC)
        cursor, new, updated = state.cursor, 0, 0
        lines: list[dict[str, Any]] = []
        async with self.db.session() as s:
            ids = [i.external_id for i in batch]
            known = {
                r.external_id: r
                for r in (
                    await s.execute(
                        select(ItemRow).where(
                            ItemRow.source_id == src.id, ItemRow.external_id.in_(ids)
                        )
                    )
                ).scalars()
            }
            for item in batch:
                cursor = connector.next_cursor(item, cursor)
                checksum = item.checksum()
                prior = known.get(item.external_id)
                if prior is not None and prior.checksum == checksum:
                    lines.append(
                        {"id": item.external_id, "checksum": checksum, "action": "unchanged"}
                    )
                    continue
                raw_uri = prior.raw_uri if prior is not None else None
                if not dry_run and item.content is not None:
                    key = _raw_key(self.opts.raw_prefix, src.key, item, now)
                    raw_uri = await self.store.put(key, item.content, item.content_type)
                action = "updated" if prior is not None else "new"
                new, updated = new + (action == "new"), updated + (action == "updated")
                lines.append(
                    {"id": item.external_id, "checksum": checksum, "action": action, "raw": raw_uri}
                )
                if dry_run:
                    continue
                values = {
                    "source_id": src.id,
                    "stream": stream,
                    "external_id": item.external_id,
                    "url": item.url,
                    "checksum": checksum,
                    "raw_uri": raw_uri,
                    "content_type": item.content_type,
                    "fetched_at": now,
                    "metadata": item.metadata,
                }
                stmt = insert(ItemRow).values(**values)
                await s.execute(
                    stmt.on_conflict_do_update(
                        constraint="uq_src_items_source_external",
                        set_={
                            k: v for k, v in values.items() if k not in ("source_id", "external_id")
                        },
                    )
                )
            if not dry_run:
                cur = insert(Cursor).values(
                    source_id=src.id, stream=stream, value=cursor, updated_at=now
                )
                await s.execute(
                    cur.on_conflict_do_update(
                        index_elements=["source_id", "stream"],
                        set_={"value": cur.excluded.value, "updated_at": cur.excluded.updated_at},
                    )
                )
        # Past this point the batch is committed: only now do the in-memory counters move.
        state.cursor, state.new, state.updated = cursor, state.new + new, state.updated + updated
        state.progress_by = name
        state.manifest.extend(lines)

    async def _finish(
        self, src: SourceInfo, stream: str, started: datetime, state: _State, out: StreamResult
    ) -> int:
        finished = datetime.now(UTC)
        manifest_uri = None
        if state.manifest:
            body = "\n".join(json.dumps(m, default=str) for m in state.manifest).encode()
            key = f"{self.opts.raw_prefix}/{src.key}/runs/{started:%Y%m%dT%H%M%S}-{stream}.jsonl"
            manifest_uri = await self.store.put(key, body, "application/x-ndjson")
        async with self.db.session() as s:
            run = Run(
                source_id=src.id,
                stream=stream,
                strategy_used=out.strategy_used,
                started_at=started,
                finished_at=finished,
                status=out.status,
                items_new=out.items_new,
                items_updated=out.items_updated,
                errors=out.errors,
                cost=out.cost,
                manifest_uri=manifest_uri,
                error_summary=out.error_summary,
            )
            s.add(run)
            await s.flush()
            if out.cost > 0:
                await record_usage(
                    s,
                    org_id=src.org_id or "platform",
                    kind="sources.paid_call",
                    units_in=out.items_new + out.items_updated,
                    source=src.key,
                    stream=stream,
                    cost=str(out.cost),
                )
            return run.id

    async def reconcile(
        self,
        source_key: str,
        stream: str,
        sample: int = 50,
        strategies: tuple[str, str] | None = None,
    ) -> ReconcileReport:
        """Fetch the first ``sample`` items through two strategies and compare. Writes nothing."""
        src, connector = await self._load(source_key)
        ctx = await self._context(src)
        impls = connector.strategies()
        usable = [n for n in src.strategies if n in impls and await impls[n].available(ctx)]
        pair = strategies or (tuple(usable[:2]) if len(usable) >= 2 else None)
        if pair is None:
            raise ValueError(f"{source_key} needs two available strategies to reconcile")
        first = await self._sample(impls[pair[0]], ctx, stream, sample)
        second = await self._sample(impls[pair[1]], ctx, stream, sample)
        both = first.keys() & second.keys()
        return ReconcileReport(
            stream=stream,
            strategies=(pair[0], pair[1]),
            compared=len(both),
            only_in_first=sorted(first.keys() - second.keys()),
            only_in_second=sorted(second.keys() - first.keys()),
            # Checksums differ between formats (HTML vs JSON), so compare the source's own
            # modification time where both strategies report one.
            different=sorted(k for k in both if first[k] and second[k] and first[k] != second[k]),
        )

    @staticmethod
    async def _sample(
        strat: Strategy, ctx: ConnectorContext, stream: str, n: int
    ) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        async for item in strat.fetch(ctx, stream, None):
            out[item.external_id] = item.updated_at.isoformat() if item.updated_at else None
            if len(out) >= n:
                break
        return out
