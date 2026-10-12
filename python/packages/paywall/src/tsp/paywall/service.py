from __future__ import annotations

import hashlib
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.cache import Cache

from . import meter
from .crawlers import Resolver, SystemResolver, verify_crawler
from .models import Credit, Gift, Policy, Read, Unlock

Reason = Literal["entitled", "free_quota", "gift", "unlocked", "crawler", "wall", "open"]
Entitled = Callable[
    [AsyncSession, str, str], Awaitable[bool]
]  # (session, user_id, org_id) -> subscribed?
DEVICE_TTL = 24 * 3600
DEVICE_CAP = 200


class InsufficientCredits(Exception):
    pass


@dataclass(frozen=True)
class Item:
    id: str
    org_id: str
    type: str = "article"


@dataclass(frozen=True)
class Visitor:
    """What the paywall needs to know about a request. Build it with ``visitor_from_request``."""

    ip: str | None = None
    user_agent: str = ""
    accept_language: str = ""
    meter_cookie: str | None = None
    gift: str | None = None
    user_id: str | None = None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: Reason
    remaining: int  # further new items this reader may open in the window; -1 = not metered
    wall: Literal["hard", "soft"] = "hard"
    set_cookie: str | None = None  # send as the pw_m cookie when present
    require_account: bool = False  # the wall here is "create a free account"
    meter_id: str | None = None  # internal: carried to record_read


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Paywall:
    """Decides whether a visitor may read an item, and counts what they read.

    Order: verified crawler, subscriber, unlocked item, gift link, free quota, wall. The anonymous
    meter is an HMAC-signed cookie. A coarse device key (network + browser + language, hashed, kept
    in the cache for a day) catches the cookie being cleared, but a key that sees more than
    ``shared_after`` different meters in a day is treated as a shared network and ignored, so an
    office, campus or mobile carrier is never walled for what its neighbours read.
    """

    secret: str
    cache: Cache
    entitled: Entitled | None = None
    resolver: Resolver = field(default_factory=SystemResolver)
    shared_after: int = 3

    # -- policies --------------------------------------------------------------------------

    async def set_policy(
        self,
        session: AsyncSession,
        org_id: str,
        name: str,
        *,
        free_items: int,
        window_days: int,
        wall: Literal["hard", "soft"] = "hard",
        applies_to: list[str] | None = None,
        require_account_after: int | None = None,
    ) -> Policy:
        if free_items < 0 or window_days < 1:
            raise ValueError("free_items must be 0 or more and window_days at least 1")
        if require_account_after is not None and require_account_after < 0:
            raise ValueError("require_account_after cannot be negative")
        p = Policy(
            org_id=org_id,
            name=name,
            free_items=free_items,
            window_days=window_days,
            wall=wall,
            applies_to=applies_to or [],
            require_account_after=require_account_after,
        )
        session.add(p)
        await session.flush()
        return p

    async def _policy(self, session: AsyncSession, item: Item) -> Policy | None:
        rows = (
            await session.execute(
                select(Policy)
                .where(Policy.org_id == item.org_id, Policy.active.is_(True))
                .order_by(Policy.id)
            )
        ).scalars()
        return next((p for p in rows if not p.applies_to or item.type in p.applies_to), None)

    # -- identity --------------------------------------------------------------------------

    async def _meter(self, v: Visitor) -> tuple[str, str | None, list[str]]:
        """(this visitor's meter id, cookie to set, other meters counted with it)."""
        existing = meter.verify(self.secret, v.meter_cookie)
        key = f"pw:alias:{existing}" if existing else None
        if existing:
            aliases = (await self.cache.get_json(key)) if key else None
            return existing, None, list(aliases or [])
        mid = meter.new_meter_id()
        dk = meter.device_key(self.secret, v.ip, v.user_agent, v.accept_language)
        seen: list[str] = list((await self.cache.get_json(f"pw:dev:{dk}")) or [])
        inherited: list[str] = []
        if len(seen) < self.shared_after:
            inherited = seen  # the same browser coming back without its cookie
        seen = (seen + [mid])[-DEVICE_CAP:]
        await self.cache.set_json(f"pw:dev:{dk}", seen, DEVICE_TTL)
        if inherited:
            await self.cache.set_json(f"pw:alias:{mid}", inherited, DEVICE_TTL * 30)
        return mid, meter.sign(self.secret, mid), inherited

    @staticmethod
    def _subject(v: Visitor, mid: str) -> list[str]:
        return ([f"u:{v.user_id}"] if v.user_id else []) + [f"m:{mid}"]

    # -- the decision ------------------------------------------------------------------------

    async def check(self, session: AsyncSession, v: Visitor, item: Item) -> Decision:
        if await verify_crawler(v.ip, v.user_agent, self.resolver):
            return Decision(True, "crawler", -1)
        mid, cookie, aliases = await self._meter(v)
        base = Decision(False, "wall", 0, set_cookie=cookie, meter_id=mid)
        if v.user_id and self.entitled and await self.entitled(session, v.user_id, item.org_id):
            return replace(base, allowed=True, reason="entitled", remaining=-1)
        subjects = self._subject(v, mid) + [f"m:{a}" for a in aliases]
        if (
            await session.execute(
                select(func.count())
                .select_from(Unlock)
                .where(
                    Unlock.org_id == item.org_id,
                    Unlock.item_id == item.id,
                    Unlock.subject.in_(subjects),
                )
            )
        ).scalar_one():
            return replace(base, allowed=True, reason="unlocked", remaining=-1)
        if v.gift and await self._redeem_gift(session, v.gift, item, subjects[0]):
            return replace(base, allowed=True, reason="gift", remaining=-1)
        policy = await self._policy(session, item)
        if policy is None:
            return replace(base, allowed=True, reason="open", remaining=-1)
        base = replace(base, wall=policy.wall)  # type: ignore[arg-type]
        meters = [mid, *aliases]
        since = _now() - timedelta(days=policy.window_days)
        who = [Read.meter_id.in_(meters)] + ([Read.user_id == v.user_id] if v.user_id else [])
        read = (
            (
                await session.execute(
                    select(Read.item_id)
                    .where(Read.org_id == item.org_id, Read.at > since, or_(*who))
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if (
            v.user_id is None
            and policy.require_account_after is not None
            and len(read) >= policy.require_account_after
            and item.id not in read
        ):
            return replace(base, require_account=True)
        if item.id in read:
            return replace(
                base,
                allowed=True,
                reason="free_quota",
                remaining=max(0, policy.free_items - len(read)),
            )
        if len(read) < policy.free_items:
            return replace(
                base, allowed=True, reason="free_quota", remaining=policy.free_items - len(read) - 1
            )
        return base

    async def record_read(self, session: AsyncSession, v: Visitor, item: Item, d: Decision) -> None:
        """Count the read. Only reads that used the free quota count; subscribers, gifts and crawlers do not."""
        if d.reason != "free_quota" or not d.allowed:
            return
        policy = await self._policy(session, item)
        if policy is None:
            return
        since = _now() - timedelta(days=policy.window_days)
        meters = [d.meter_id] if d.meter_id else []
        who: list[ColumnElement[bool]] = [Read.meter_id.in_(meters)] if meters else []
        if v.user_id:
            who.append(Read.user_id == v.user_id)
        if (
            who
            and (
                await session.execute(
                    select(func.count())
                    .select_from(Read)
                    .where(
                        Read.org_id == item.org_id,
                        Read.item_id == item.id,
                        Read.at > since,
                        or_(*who),
                    )
                )
            ).scalar_one()
        ):
            return  # already counted in this window
        session.add(
            Read(org_id=item.org_id, user_id=v.user_id, meter_id=d.meter_id, item_id=item.id)
        )

    async def purge(self, session: AsyncSession, *, keep_days: int = 400) -> int:
        """Forget reads older than ``keep_days`` (the retention schedule says 13 months) and spent gift links."""
        cutoff = _now() - timedelta(days=keep_days)
        gone = await session.execute(delete(Read).where(Read.at < cutoff))
        await session.execute(delete(Gift).where(Gift.expires_at < cutoff))
        return int(gone.rowcount or 0)  # type: ignore[attr-defined]

    # -- gifts and credits ---------------------------------------------------------------------

    async def create_gift(
        self,
        session: AsyncSession,
        user_id: str,
        item: Item,
        *,
        max_uses: int = 3,
        days: int = 7,
        limit_per_day: int = 10,
    ) -> str:
        """A link token (put it in ``?g=``). Only the hash is stored."""
        since = _now() - timedelta(days=1)
        made = (
            await session.execute(
                select(func.count())
                .select_from(Gift)
                .where(Gift.giver_user_id == user_id, Gift.expires_at > since)
            )
        ).scalar_one()
        if made >= limit_per_day:
            raise ValueError("too many gift links today")
        token = secrets.token_urlsafe(18)
        session.add(
            Gift(
                token_hash=hash_token(token),
                org_id=item.org_id,
                item_id=item.id,
                giver_user_id=user_id,
                max_uses=max(1, max_uses),
                expires_at=_now() + timedelta(days=days),
            )
        )
        await session.flush()
        return token

    async def _redeem_gift(
        self, session: AsyncSession, token: str, item: Item, subject: str
    ) -> bool:
        gift = await session.get(Gift, hash_token(token))
        if (
            gift is None
            or gift.org_id != item.org_id
            or gift.item_id != item.id
            or gift.expires_at < _now()
        ):
            return False
        have = (
            await session.execute(
                select(func.count())
                .select_from(Unlock)
                .where(
                    Unlock.subject == subject,
                    Unlock.org_id == item.org_id,
                    Unlock.item_id == item.id,
                )
            )
        ).scalar_one()
        if have:
            return True  # this reader already used the gift; coming back is free
        claimed = await session.execute(
            update(Gift)
            .where(Gift.token_hash == gift.token_hash, Gift.uses < Gift.max_uses)
            .values(uses=Gift.uses + 1)
            .returning(Gift.uses)
        )
        if claimed.first() is None:
            return False
        await session.execute(
            insert(Unlock)
            .values(subject=subject, org_id=item.org_id, item_id=item.id, via="gift")
            .on_conflict_do_nothing()
        )
        return True

    async def add_credits(self, session: AsyncSession, user_id: str, org_id: str, n: int) -> int:
        """Call this from the payments receipt hook when someone buys a credit pack."""
        if n <= 0:
            raise ValueError("n must be positive")
        row = await session.execute(
            insert(Credit)
            .values(user_id=user_id, org_id=org_id, balance=n)
            .on_conflict_do_update(
                index_elements=["user_id", "org_id"], set_={"balance": Credit.balance + n}
            )
            .returning(Credit.balance)
        )
        return int(row.scalar_one())

    async def balance(self, session: AsyncSession, user_id: str, org_id: str) -> int:
        c = await session.get(Credit, (user_id, org_id))
        return c.balance if c else 0

    async def unlock(self, session: AsyncSession, user_id: str, item: Item) -> bool:
        """Spend one credit to open an item for good. True when it was newly unlocked, False if it already was."""
        exists = await session.execute(
            select(Unlock.id).where(
                Unlock.subject == f"u:{user_id}",
                Unlock.org_id == item.org_id,
                Unlock.item_id == item.id,
            )
        )
        if exists.first():
            return False
        spent = await session.execute(
            update(Credit)
            .where(Credit.user_id == user_id, Credit.org_id == item.org_id, Credit.balance >= 1)
            .values(balance=Credit.balance - 1)
            .returning(Credit.balance)
        )
        if spent.first() is None:
            raise InsufficientCredits("no credits left")
        session.add(
            Unlock(subject=f"u:{user_id}", org_id=item.org_id, item_id=item.id, via="credit")
        )
        await session.flush()
        return True
