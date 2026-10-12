"""Prepaid credits: an append-only ledger plus a balance that can never go negative."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CreditAccount, CreditEntry


class InsufficientCredits(Exception):
    def __init__(self, balance: int, needed: int):
        super().__init__(f"balance {balance} is below the {needed} needed")
        self.balance, self.needed = balance, needed


async def balance(session: AsyncSession, org_id: str) -> int:
    acct = await session.get(CreditAccount, org_id)
    return int(acct.balance) if acct else 0


async def _apply(
    session: AsyncSession,
    org_id: str,
    delta: int,
    reason: str,
    ref: str | None,
    meta: dict[str, Any] | None,
) -> int:
    await session.execute(
        text("INSERT INTO credit_accounts (org_id, balance) VALUES (:o, 0) ON CONFLICT DO NOTHING"),
        {"o": org_id},
    )
    # Locking the account row serializes every movement for this organization.
    current = int(
        (
            await session.execute(
                text("SELECT balance FROM credit_accounts WHERE org_id = :o FOR UPDATE"),
                {"o": org_id},
            )
        ).scalar_one()
    )
    if ref is not None:
        seen = (
            await session.execute(
                select(CreditEntry.id).where(CreditEntry.org_id == org_id, CreditEntry.ref == ref)
            )
        ).first()
        if seen:
            return current  # same operation delivered twice: no second effect
    new = current + delta
    if new < 0:
        raise InsufficientCredits(current, -delta)
    await session.execute(
        text("UPDATE credit_accounts SET balance = :b WHERE org_id = :o"), {"b": new, "o": org_id}
    )
    session.add(
        CreditEntry(
            org_id=org_id, delta=delta, balance_after=new, reason=reason, ref=ref, meta=meta or {}
        )
    )
    await session.flush()
    return new


async def grant(
    session: AsyncSession,
    org_id: str,
    amount: int,
    *,
    reason: str,
    ref: str | None = None,
    meta: dict[str, Any] | None = None,
) -> int:
    if amount <= 0:
        raise ValueError("grant amount must be positive")
    return await _apply(session, org_id, amount, reason, ref, meta)


async def spend(
    session: AsyncSession,
    org_id: str,
    amount: int,
    *,
    reason: str,
    ref: str | None = None,
    meta: dict[str, Any] | None = None,
) -> int:
    """Take credits or raise ``InsufficientCredits``. Pass a ``ref`` (request id) to make retries safe."""
    if amount <= 0:
        raise ValueError("spend amount must be positive")
    return await _apply(session, org_id, -amount, reason, ref, meta)
