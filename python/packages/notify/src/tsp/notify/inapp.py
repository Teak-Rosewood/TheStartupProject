from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DeviceToken, Notification


async def notify_user(
    session: AsyncSession,
    user_sub: str,
    kind: str,
    title: str,
    body: str = "",
    url: str | None = None,
) -> int:
    n = Notification(user_sub=user_sub, kind=kind, title=title, body=body, url=url)
    session.add(n)
    await session.flush()
    return n.id


async def unread_count(session: AsyncSession, user_sub: str) -> int:
    rows = await session.execute(
        select(Notification.id).where(
            Notification.user_sub == user_sub, Notification.read_at.is_(None)
        )
    )
    return len(rows.all())


async def mark_read(session: AsyncSession, user_sub: str, ids: list[int] | None = None) -> int:
    stmt = update(Notification).where(
        Notification.user_sub == user_sub, Notification.read_at.is_(None)
    )
    if ids is not None:
        stmt = stmt.where(Notification.id.in_(ids))
    result = await session.execute(
        stmt.values(read_at=datetime.now(timezone.utc)).returning(Notification.id)
    )
    return len(result.all())


async def register_device(session: AsyncSession, user_sub: str, token: str, platform: str) -> None:
    """A device token belongs to whoever signed in last on that device."""
    stmt = insert(DeviceToken).values(token=token, user_sub=user_sub, platform=platform)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["token"], set_={"user_sub": user_sub, "platform": platform}
        )
    )


async def user_tokens(session: AsyncSession, user_sub: str) -> list[str]:
    return list(
        (
            await session.execute(select(DeviceToken.token).where(DeviceToken.user_sub == user_sub))
        ).scalars()
    )


async def forget_tokens(session: AsyncSession, tokens: list[str]) -> None:
    if tokens:
        await session.execute(DeviceToken.__table__.delete().where(DeviceToken.token.in_(tokens)))  # type: ignore[attr-defined]
