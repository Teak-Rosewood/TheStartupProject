from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Observation, Series


@dataclass(frozen=True)
class Comparison:
    """First period against last. Values are the last observation inside each period."""

    periods: list[tuple[date, date]]
    values: list[float]
    absolute: float
    pct: float | None  # None when the first value is 0
    cagr: float | None  # per year between the period ends; None when undefined
    adjusted_by: str | None = None  # deflator series key, when the values are in first-period money


class SeriesService:
    """Numeric time series that keep every revision.

    A value is identified by (series, period, estimate type, vintage). Re-posting the same four
    keeps the stored row; posting a revised number with a new ``vintage`` adds a row, so you can
    always ask what was believed on a given date.
    """

    def __init__(self, session: AsyncSession):
        self.s = session

    async def upsert_series(
        self,
        org_id: str,
        key: str,
        name: str,
        unit: str | None = None,
        frequency: str | None = None,
        source_ref: str | None = None,
        attrs: Mapping[str, Any] | None = None,
    ) -> Series:
        row = (
            await self.s.execute(select(Series).where(Series.org_id == org_id, Series.key == key))
        ).scalar_one_or_none()
        if row is None:
            row = Series(org_id=org_id, key=key, name=name)
            self.s.add(row)
        row.name, row.unit, row.frequency, row.source_ref = name, unit, frequency, source_ref
        row.attrs = dict(attrs or {})
        await self.s.flush()
        return row

    async def add_observations(
        self, org_id: str, series_id: uuid.UUID, rows: Iterable[Mapping[str, Any]]
    ) -> int:
        """Rows: ``period_start``, ``period_end`` (default: same day), ``value``, optional
        ``estimate_type`` (default ``actual``), ``vintage`` (default today) and ``attrs``.
        Returns how many were new."""
        values = [
            {
                "org_id": org_id,
                "series_id": series_id,
                "period_start": r["period_start"],
                "period_end": r.get("period_end") or r["period_start"],
                "value": r["value"],
                "estimate_type": r.get("estimate_type", "actual"),
                "vintage": r.get("vintage") or date.today(),
                "attrs": dict(r.get("attrs") or {}),
            }
            for r in rows
        ]
        if not values:
            return 0
        done = await self.s.execute(
            insert(Observation)
            .values(values)
            .on_conflict_do_nothing()
            .returning(Observation.period_start)
        )
        return len(done.all())

    async def get(
        self,
        org_id: str,
        series_key: str,
        start: date | None = None,
        end: date | None = None,
        estimate_type: str = "latest",
        as_of: date | None = None,
    ) -> list[Observation]:
        """One observation per period. ``estimate_type="latest"`` takes the newest vintage of any
        type; a named type (``estimate``, ``actual``...) takes the newest vintage of that type.
        ``as_of`` ignores vintages after that date: what was known then."""
        rows = await self.s.execute(
            text(
                """
                SELECT DISTINCT ON (o.period_start) o.*
                FROM g_observations o JOIN g_series s ON s.id = o.series_id
                WHERE s.org_id = :org AND s.key = :key
                  AND (CAST(:start AS date) IS NULL OR o.period_end >= :start)
                  AND (CAST(:end AS date) IS NULL OR o.period_start <= :end)
                  AND (:etype = 'latest' OR o.estimate_type = :etype)
                  AND (CAST(:as_of AS date) IS NULL OR o.vintage <= :as_of)
                ORDER BY o.period_start, o.vintage DESC
                """
            ),
            {
                "org": org_id,
                "key": series_key,
                "start": start,
                "end": end,
                "etype": estimate_type,
                "as_of": as_of,
            },
        )
        return [Observation(**dict(r._mapping)) for r in rows]

    async def _level(
        self, org_id: str, key: str, period: tuple[date, date], estimate_type: str
    ) -> float:
        obs = await self.get(org_id, key, period[0], period[1], estimate_type)
        obs = [o for o in obs if period[0] <= o.period_start <= period[1]]
        if not obs:
            raise LookupError(
                f"series {key!r} has no observation between {period[0]} and {period[1]}"
            )
        return float(obs[-1].value)

    async def compare(
        self,
        org_id: str,
        series_key: str,
        periods: list[tuple[date, date]],
        deflator_key: str | None = None,
        estimate_type: str = "latest",
    ) -> Comparison:
        """Absolute change, percent change and compound annual growth from the first period to the
        last. With ``deflator_key`` (a price index) every value is first restated in the money of the
        first period, so the change is real, not nominal."""
        if len(periods) < 2:
            raise ValueError("compare needs at least two periods")
        values = [await self._level(org_id, series_key, p, estimate_type) for p in periods]
        if deflator_key:
            index = [await self._level(org_id, deflator_key, p, "latest") for p in periods]
            if min(index) <= 0:
                raise ValueError("deflator values must be positive")
            values = [v * index[0] / i for v, i in zip(values, index, strict=True)]
        first, last = values[0], values[-1]
        years = (periods[-1][1] - periods[0][1]).days / 365.25
        cagr = (last / first) ** (1 / years) - 1 if first > 0 and last > 0 and years > 0 else None
        return Comparison(
            periods=periods,
            values=values,
            absolute=last - first,
            pct=(last / first - 1) * 100 if first else None,
            cagr=cagr * 100 if cagr is not None else None,
            adjusted_by=deflator_key,
        )
