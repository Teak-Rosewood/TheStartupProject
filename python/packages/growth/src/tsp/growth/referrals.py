from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Referral, ReferralCode

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: codes get read aloud and retyped


async def code_for(session: AsyncSession, owner_sub: str) -> str:
    existing = (
        await session.execute(select(ReferralCode.code).where(ReferralCode.owner_sub == owner_sub))
    ).scalar_one_or_none()
    if existing:
        return existing
    for _ in range(10):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        got = await session.execute(
            insert(ReferralCode)
            .values(code=code, owner_sub=owner_sub)
            .on_conflict_do_nothing()
            .returning(ReferralCode.code)
        )
        if got.first():
            return code
        again = (
            await session.execute(
                select(ReferralCode.code).where(ReferralCode.owner_sub == owner_sub)
            )
        ).scalar_one_or_none()
        if again:  # a concurrent request created it
            return again
    raise RuntimeError("could not allocate a referral code")


async def record(
    session: AsyncSession,
    code: str,
    referred_sub: str,
    on_referral: Callable[[AsyncSession, str, str], Awaitable[None]] | None = None,
) -> bool:
    """Credit a referral once per new user. Unknown codes and self-referrals are ignored."""
    owner = (
        await session.execute(
            select(ReferralCode.owner_sub).where(ReferralCode.code == code.strip().upper())
        )
    ).scalar_one_or_none()
    if owner is None or owner == referred_sub:
        return False
    got = await session.execute(
        insert(Referral)
        .values(code=code.strip().upper(), referred_sub=referred_sub)
        .on_conflict_do_nothing()
        .returning(Referral.id)
    )
    if got.first() is None:
        return False
    if on_referral:
        await on_referral(
            session, owner, referred_sub
        )  # reward logic is yours: credits, a discount, a badge
    return True


async def count(session: AsyncSession, owner_sub: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Referral)
                .join(ReferralCode, ReferralCode.code == Referral.code)
                .where(ReferralCode.owner_sub == owner_sub)
            )
        ).scalar_one()
    )
