from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Poll, Response


class PollError(Exception):
    """A response the poll does not accept; the message says why."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _axis(axes: dict[str, Any], name: str) -> tuple[float, float]:
    a = axes[name]
    return float(a["min"]), float(a["max"])


class Polls:
    """Where a reader stands on a scale before reading and again after. Answers are final: a reader
    cannot revise the 'before' once they have seen the argument."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] = _utcnow,
        min_k: int = 20,
        bins: int = 10,
    ):
        self.s, self.now, self.min_k, self.bins = session, clock, min_k, bins

    async def create(
        self,
        org_id: str,
        item_id: str,
        kind: str,
        axes: dict[str, Any],
        *,
        opens_at: datetime | None = None,
        closes_at: datetime | None = None,
    ) -> Poll:
        if kind not in ("scale1d", "scale2d"):
            raise PollError("kind must be scale1d or scale2d")
        for name in ("x", "y") if kind == "scale2d" else ("x",):
            if (
                name not in axes
                or not {"min", "max"} <= set(axes[name])
                or axes[name]["min"] >= axes[name]["max"]
            ):
                raise PollError(f"axis {name} needs min below max")
        if opens_at and closes_at and closes_at <= opens_at:
            raise PollError("a poll must close after it opens")
        p = Poll(
            org_id=org_id,
            item_id=item_id,
            kind=kind,
            axes=axes,
            opens_at=opens_at,
            closes_at=closes_at,
        )
        self.s.add(p)
        await self.s.flush()
        return p

    async def get(self, poll_id: uuid.UUID) -> Poll:
        p = await self.s.get(Poll, poll_id)
        if p is None:
            raise LookupError("no such poll")
        return p

    async def for_item(self, org_id: str, item_id: str) -> list[Poll]:
        rows = await self.s.execute(
            select(Poll).where(Poll.org_id == org_id, Poll.item_id == item_id)
        )
        return list(rows.scalars())

    async def respond(
        self, user_id: str, poll_id: uuid.UUID, phase: str, x: float, y: float | None = None
    ) -> Response:
        p = await self.get(poll_id)
        if phase not in ("before", "after"):
            raise PollError("phase must be before or after")
        now = self.now()
        if (p.opens_at and now < p.opens_at) or (p.closes_at and now >= p.closes_at):
            raise PollError("this poll is not open")
        lo, hi = _axis(p.axes, "x")
        if not lo <= x <= hi:
            raise PollError(f"x must be between {lo:g} and {hi:g}")
        if p.kind == "scale2d":
            if y is None:
                raise PollError("this poll needs both x and y")
            ylo, yhi = _axis(p.axes, "y")
            if not ylo <= y <= yhi:
                raise PollError(f"y must be between {ylo:g} and {yhi:g}")
        elif y is not None:
            raise PollError("this poll has one axis")
        have = {r.phase for r in await self._mine(user_id, poll_id)}
        if phase in have:
            raise PollError(f"you already answered {phase}")
        if phase == "after" and "before" not in have and p.axes.get("before_required", True):
            raise PollError("answer before reading first")
        r = Response(poll_id=poll_id, user_id=user_id, phase=phase, x=x, y=y, at=now)
        self.s.add(r)
        await self.s.flush()
        return r

    async def _mine(self, user_id: str, poll_id: uuid.UUID) -> list[Response]:
        rows = await self.s.execute(
            select(Response).where(Response.poll_id == poll_id, Response.user_id == user_id)
        )
        return list(rows.scalars())

    async def shift(self, user_id: str, poll_id: uuid.UUID) -> tuple[float, float | None] | None:
        """(dx, dy) from before to after, or None until both are answered. dy is None on one-axis polls."""
        mine = {r.phase: r for r in await self._mine(user_id, poll_id)}
        if "before" not in mine or "after" not in mine:
            return None
        b, a = mine["before"], mine["after"]
        return a.x - b.x, (a.y - b.y) if a.y is not None and b.y is not None else None

    def _hist(self, values: list[tuple[float, float | None]], poll: Poll) -> list[Any]:
        lo, hi = _axis(poll.axes, "x")

        def bucket(v: float, lo_: float, hi_: float) -> int:
            return min(self.bins - 1, int((v - lo_) / (hi_ - lo_) * self.bins))

        if poll.kind == "scale1d":
            h = [0] * self.bins
            for x, _ in values:
                h[bucket(x, lo, hi)] += 1
            return h
        ylo, yhi = _axis(poll.axes, "y")
        grid = [[0] * self.bins for _ in range(self.bins)]
        for x, y in values:
            grid[bucket(y if y is not None else ylo, ylo, yhi)][bucket(x, lo, hi)] += 1
        return grid

    async def aggregate(self, poll_id: uuid.UUID) -> dict[str, Any]:
        """Histograms for before and after, and the mean shift. Anything based on fewer than ``min_k``
        people is withheld, so a small crowd cannot be read person by person."""
        p = await self.get(poll_id)
        rows = (
            (await self.s.execute(select(Response).where(Response.poll_id == poll_id)))
            .scalars()
            .all()
        )
        by_phase: dict[str, dict[str, Response]] = {"before": {}, "after": {}}
        for r in rows:
            by_phase[r.phase][r.user_id] = r
        out: dict[str, Any] = {
            "n_before": len(by_phase["before"]), "n_after": len(by_phase["after"]),
            "before": None, "after": None, "mean_shift": None, "min_k": self.min_k,
        }  # fmt: skip
        for phase in ("before", "after"):
            if len(by_phase[phase]) >= self.min_k:
                out[phase] = self._hist([(r.x, r.y) for r in by_phase[phase].values()], p)
        both = by_phase["before"].keys() & by_phase["after"].keys()
        if len(both) >= self.min_k:
            n = len(both)
            dx = sum(by_phase["after"][u].x - by_phase["before"][u].x for u in both) / n
            ys = [(by_phase["after"][u].y, by_phase["before"][u].y) for u in both]
            dy = (
                sum(a - b for a, b in ys if a is not None and b is not None) / n
                if p.kind == "scale2d"
                else None
            )
            out["mean_shift"] = {
                "n": n,
                "dx": round(dx, 3),
                "dy": round(dy, 3) if dy is not None else None,
            }
        return out

    async def history(self, user_id: str) -> list[dict[str, Any]]:
        """The reader's own answers and shifts. Only ever returned to that reader."""
        rows = (
            (
                await self.s.execute(
                    select(Response).where(Response.user_id == user_id).order_by(Response.at)
                )
            )
            .scalars()
            .all()
        )
        polls: dict[uuid.UUID, dict[str, Any]] = {}
        for r in rows:
            e = polls.setdefault(r.poll_id, {"poll_id": str(r.poll_id)})
            e[r.phase] = {"x": r.x, "y": r.y, "at": r.at}
        for pid, e in polls.items():
            e["shift"] = await self.shift(user_id, pid)
        return list(polls.values())
