"""Validate and store analytics events, keeping personal data out of them."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Event

NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
ANON = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_PROPERTIES_BYTES = 4096
MAX_BATCH = 50
# Property names that signal personal data. Matching events are stored with these keys removed.
PII_KEYS = re.compile(
    r"(?i)(^|[_\-.])(e-?mail|phone|mobile|contact|password|passwd|token|secret|card(_?number)?|aadhaar|pan|gstin|address|ssn|dob"
    r"|(first|last|full|user|customer|display)_?name)($|[_\-.])|^name$"
)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def sanitize(props: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Drop properties that look like personal data. Returns the clean dict and the dropped key names."""
    clean: dict[str, Any] = {}
    dropped: list[str] = []
    for k, v in props.items():
        if PII_KEYS.search(str(k)) or (
            isinstance(v, str) and (_EMAIL.search(v) or _PHONE.fullmatch(v.strip()))
        ):
            dropped.append(str(k))
        elif isinstance(v, (str, int, float, bool)) or v is None:
            clean[str(k)[:64]] = v[:200] if isinstance(v, str) else v
        else:
            dropped.append(str(k))  # nested structures are not part of the schema
    return clean, dropped


class InvalidEvent(ValueError):
    pass


def validate(raw: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    name, anon = str(raw.get("name", "")), str(raw.get("anonymous_id", ""))
    if not NAME.match(name):
        raise InvalidEvent(f"event name {name!r} must be lowercase snake_case")
    if not ANON.match(anon):
        raise InvalidEvent("anonymous_id must be 8-64 URL-safe characters")
    props = raw.get("properties")
    props = {} if props is None else props
    if not isinstance(props, dict) or len(json.dumps(props)) > MAX_PROPERTIES_BYTES:
        raise InvalidEvent("properties must be an object under 4 KB")
    clean, _ = sanitize(props)
    ts = now or datetime.now(UTC)
    if raw.get(
        "ts"
    ):  # clients may report when it happened, but never in the future or older than a day
        try:
            claimed = datetime.fromisoformat(str(raw["ts"]).replace("Z", "+00:00"))
            if claimed.tzinfo and 0 <= (ts - claimed).total_seconds() <= 86400:
                ts = claimed
        except ValueError:
            pass
    path = str(raw["path"])[:200] if raw.get("path") else None
    return {"name": name, "anonymous_id": anon, "properties": clean, "ts": ts, "path": path}


async def store(
    session: AsyncSession,
    events: list[dict[str, Any]],
    *,
    user_sub: str | None = None,
    org_id: str | None = None,
) -> int:
    if len(events) > MAX_BATCH:
        raise InvalidEvent(f"at most {MAX_BATCH} events per request")
    rows = [validate(e) for e in events]  # all or nothing: one bad event rejects the batch
    for r in rows:
        session.add(Event(user_sub=user_sub, org_id=org_id, **r))
    await session.flush()
    return len(rows)


async def identify(session: AsyncSession, anonymous_id: str, user_sub: str) -> int:
    """After sign-in, attach the visitor's earlier anonymous events to the account (stitching)."""
    if not ANON.match(anonymous_id):
        raise InvalidEvent("bad anonymous_id")
    res = await session.execute(
        update(Event)
        .where(Event.anonymous_id == anonymous_id, Event.user_sub.is_(None))
        .values(user_sub=user_sub)
        .returning(Event.id)
    )
    return len(res.all())
