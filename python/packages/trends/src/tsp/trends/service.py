from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record
from tsp.crawl import HttpFetcher
from tsp.db import Database

from .models import CandidateRow, SignalRow, TopicRow
from .scorer import BASELINE_DAYS, Point, Scored, combine, dedupe, series_z
from .signals import Signal, SignalPoint, TrendsContext

log = logging.getLogger(__name__)

Embed = Callable[[list[str]], Awaitable[list[list[float]]]]
Notify = Callable[[str, date, Sequence[CandidateRow]], Awaitable[None]]


class TrendsError(Exception):
    status = 400
    type = "bad_request"


class NotFound(TrendsError):
    status = 404
    type = "not_found"


class Conflict(TrendsError):
    status = 409
    type = "conflict"


@dataclass
class TrendsConfig:
    weights: Mapping[str, float] = field(default_factory=dict)  # per signal name, default 1.0
    min_score: float = 2.0  # candidates below this are not written
    max_candidates: int = 50
    dedupe_threshold: float = 0.9
    lookback_days: int = BASELINE_DAYS + 2  # collection window on the first run
    keep_days: int = 60  # signal rows older than this are deleted by ``prune``


@dataclass
class TrendsService:
    """Collect points per signal, score them each day, keep the editor's list.

    ``embed`` (texts to vectors, for example ``tsp.vector``'s embedder) enables near-duplicate
    merging by meaning; without it labels merge only when their words are identical.
    ``notify(org_id, day, candidates)`` is called after a scan wrote new candidates."""

    db: Database
    http: HttpFetcher
    signals: Mapping[str, Signal]
    config: TrendsConfig = field(default_factory=TrendsConfig)
    embed: Embed | None = None
    notify: Notify | None = None
    secrets: Mapping[str, str] = field(default_factory=dict)

    # -- topics ------------------------------------------------------------------------------

    async def upsert_topic(
        self,
        session: AsyncSession,
        org_id: str,
        key: str,
        label: str,
        keywords: Sequence[str],
        *,
        langs: Sequence[str] = (),
        regions: Sequence[str] = (),
        active: bool = True,
    ) -> TopicRow:
        ins = insert(TopicRow).values(
            org_id=org_id, key=key, label=label, keywords=list(keywords), langs=list(langs),
            regions=list(regions), active=active, id=_id(),
        )  # fmt: skip
        stmt = ins.on_conflict_do_update(
            constraint="uq_tr_topics_org_key",
            set_={
                "label": label,
                "keywords": list(keywords),
                "langs": list(langs),
                "regions": list(regions),
                "active": active,
            },
        ).returning(TopicRow.id)
        topic_id = (await session.execute(stmt)).scalar_one()
        row = await session.get(TopicRow, topic_id, populate_existing=True)
        assert row is not None
        return row

    async def _topics(self, session: AsyncSession, org_id: str) -> list[TopicRow]:
        q = select(TopicRow).where(TopicRow.org_id == org_id, TopicRow.active.is_(True))
        return list((await session.execute(q.order_by(TopicRow.key))).scalars())

    async def orgs(self) -> list[str]:
        async with self.db.session() as s:
            q = select(TopicRow.org_id).where(TopicRow.active.is_(True)).distinct()
            return list((await s.execute(q.order_by(TopicRow.org_id))).scalars())

    @staticmethod
    def _as_topics(rows: Sequence[TopicRow]) -> list[Any]:
        from .signals import Topic

        return [
            Topic(r.id, r.key, r.label, tuple(r.keywords), tuple(r.langs), tuple(r.regions))
            for r in rows
        ]

    # -- collecting --------------------------------------------------------------------------

    async def collect(self, org_id: str, signal: str, *, now: datetime | None = None) -> int:
        """Run one signal for one organization and store its points. Returns the number stored."""
        now = now or datetime.now(UTC)
        impl = self.signals[signal]
        async with self.db.session() as s:
            topics = self._as_topics(await self._topics(s, org_id))
            last = (
                await s.execute(
                    select(SignalRow.at)
                    .where(SignalRow.org_id == org_id, SignalRow.signal == signal)
                    .order_by(SignalRow.at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if not topics:
            return 0
        # Re-read the last day so a late-arriving item still counts; the first run reads the whole baseline.
        since = (
            (last - timedelta(days=1)) if last else now - timedelta(days=self.config.lookback_days)
        )
        points = await impl.collect(TrendsContext(self.http, self.db, self.secrets), topics, since)
        async with self.db.session() as s:
            if points:
                lo = min(p.at for p in points)
                await s.execute(
                    delete(SignalRow).where(
                        SignalRow.org_id == org_id, SignalRow.signal == signal, SignalRow.at >= lo
                    )
                )  # replace the overlap instead of double counting it
                s.add_all(self._row(org_id, signal, p) for p in points)
        return len(points)

    @staticmethod
    def _row(org_id: str, signal: str, p: SignalPoint) -> SignalRow:
        attrs = dict(p.attrs)
        if p.label:
            attrs["label"] = p.label
        return SignalRow(
            org_id=org_id,
            topic_id=p.topic_id,
            signal=signal,
            region=p.region,
            at=p.at,
            value=Decimal(str(p.value)),
            attrs=attrs,
        )

    async def collect_all(self, *, now: datetime | None = None) -> dict[str, int]:
        """Every signal for every organization with topics. A failing signal is logged and skipped."""
        done: dict[str, int] = {}
        for org in await self.orgs():
            for name in self.signals:
                try:
                    done[f"{org}/{name}"] = await self.collect(org, name, now=now)
                except Exception:
                    log.exception("trends collect failed for %s/%s", org, name)
        return done

    # -- scoring -----------------------------------------------------------------------------

    async def scan(self, org_id: str, day: date, *, notify: bool = True) -> list[CandidateRow]:
        """Score ``day`` (the 24 hours ending at the end of that UTC day) and write the candidate list.

        Re-running replaces the scores of candidates still ``new`` and leaves picked and dismissed
        ones alone."""
        day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)
        since = day_end - timedelta(days=BASELINE_DAYS + 1)
        async with self.db.session() as s:
            topics = {t.id: t for t in await self._topics(s, org_id)}
            rows = (
                (
                    await s.execute(
                        select(SignalRow).where(
                            SignalRow.org_id == org_id,
                            SignalRow.at > since,
                            SignalRow.at <= day_end,
                        )
                    )
                )
                .scalars()
                .all()
            )
        groups: dict[tuple[str, str], dict[str, list[Point]]] = {}
        for r in rows:
            if r.topic_id is not None and r.topic_id not in topics:
                continue  # inactive or removed topic
            key = r.topic_id or "term:" + str(r.attrs.get("label", ""))
            groups.setdefault((key, r.topic_id or ""), {}).setdefault(r.signal, []).append(
                Point(r.at, float(r.value))
            )
        scored: list[Scored] = []
        for (key, topic_id), by_signal in groups.items():
            z = {
                name: v
                for name, pts in by_signal.items()
                if (v := series_z(pts, day_end)) is not None
            }
            score = combine(z, self.config.weights)
            if score >= self.config.min_score:
                label = topics[topic_id].label if topic_id else key.removeprefix("term:")
                scored.append(
                    Scored(
                        key, label, topic_id or None, score, {k: round(v, 3) for k, v in z.items()}
                    )
                )
        scored = scored[:]  # dedupe may reorder
        vectors = await self.embed([c.label for c in scored]) if self.embed and scored else None
        final = dedupe(scored, vectors, self.config.dedupe_threshold)
        final.sort(key=lambda c: -c.score)
        final = final[: self.config.max_candidates]
        async with self.db.session() as s:
            fresh: list[CandidateRow] = []
            for c in final:
                existing = (
                    await s.execute(
                        select(CandidateRow).where(
                            CandidateRow.org_id == org_id,
                            CandidateRow.day == day,
                            CandidateRow.label == c.label,
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    row = CandidateRow(
                        org_id=org_id,
                        day=day,
                        topic_id=c.topic_id,
                        label=c.label,
                        score=c.score,
                        components=c.components,
                    )
                    s.add(row)
                    fresh.append(row)
                elif existing.status == "new":
                    existing.score, existing.components = c.score, c.components
            await s.flush()
            result = list(
                (
                    await s.execute(
                        select(CandidateRow)
                        .where(CandidateRow.org_id == org_id, CandidateRow.day == day)
                        .order_by(CandidateRow.score.desc())
                    )
                ).scalars()
            )
        if notify and fresh and self.notify:
            try:
                await self.notify(org_id, day, fresh)
            except Exception:
                log.exception("trends notify failed for %s", org_id)
        return result

    async def scan_all(self, day: date) -> dict[str, int]:
        out = {}
        for org in await self.orgs():
            out[org] = len(await self.scan(org, day))
        return out

    async def prune(self, *, now: datetime | None = None) -> None:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.config.keep_days)
        async with self.db.session() as s:
            await s.execute(delete(SignalRow).where(SignalRow.at < cutoff))

    # -- the editor's list -------------------------------------------------------------------

    async def candidates(self, session: AsyncSession, org_id: str, day: date) -> list[CandidateRow]:
        q = select(CandidateRow).where(CandidateRow.org_id == org_id, CandidateRow.day == day)
        return list((await session.execute(q.order_by(CandidateRow.score.desc()))).scalars())

    async def _decide(
        self,
        session: AsyncSession,
        org_id: str,
        candidate_id: str,
        status: str,
        user: str,
        note: str | None,
    ) -> CandidateRow:
        row = await session.get(CandidateRow, candidate_id)
        if row is None or row.org_id != org_id:
            raise NotFound("no such candidate")
        if row.status != "new":
            raise Conflict(f"already {row.status}")
        row.status, row.picked_by, row.note = status, user, note
        await record(
            session,
            f"trends.candidate_{status}",
            actor=Actor(user),
            target=("trend_candidate", candidate_id),
            label=row.label,
        )
        return row

    async def pick(
        self,
        session: AsyncSession,
        org_id: str,
        candidate_id: str,
        user: str,
        note: str | None = None,
    ) -> CandidateRow:
        return await self._decide(session, org_id, candidate_id, "picked", user, note)

    async def dismiss(
        self,
        session: AsyncSession,
        org_id: str,
        candidate_id: str,
        user: str,
        note: str | None = None,
    ) -> CandidateRow:
        return await self._decide(session, org_id, candidate_id, "dismissed", user, note)


def _id() -> str:
    import uuid

    return uuid.uuid4().hex
