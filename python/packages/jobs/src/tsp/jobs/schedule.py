"""Schedules for recurring jobs, in UTC: ``every 15m``, ``every 2h``, ``every 30s``, ``daily 02:30``."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_EVERY = re.compile(r"^every (\d+)([smhd])$")
_DAILY = re.compile(r"^daily ([01]\d|2[0-3]):([0-5]\d)$")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_schedule(spec: str) -> str:
    """Return the normalized schedule or raise ValueError."""
    s = spec.strip().lower()
    m = _EVERY.match(s)
    if m and int(m.group(1)) > 0:
        return s
    if _DAILY.match(s):
        return s
    raise ValueError(f"unsupported schedule {spec!r}; use 'every 15m' or 'daily 02:30' (UTC)")


def next_run(spec: str, after: datetime) -> datetime:
    """The first run strictly after ``after``."""
    s = parse_schedule(spec)
    after = after.astimezone(timezone.utc)
    m = _EVERY.match(s)
    if m:
        return after + timedelta(seconds=int(m.group(1)) * _UNIT[m.group(2)])
    hh, mm = (int(x) for x in s.removeprefix("daily ").split(":"))
    candidate = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return candidate if candidate > after else candidate + timedelta(days=1)
