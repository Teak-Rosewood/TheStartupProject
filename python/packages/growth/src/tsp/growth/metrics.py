"""Business numbers for the staff console. Reads the tables of accounts, payments and growth directly:
it is reporting, so it is allowed to look across modules. All money is in minor units."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ACTIVE = "('active','authenticated')"


async def overview(session: AsyncSession) -> dict[str, Any]:
    async def one(sql: str) -> int:
        return int((await session.execute(text(sql))).scalar_one() or 0)

    active = await one(f"SELECT count(*) FROM subscriptions WHERE status IN {_ACTIVE}")
    mrr = await one(
        f"SELECT COALESCE(SUM(CASE p.interval WHEN 'year' THEN p.amount / 12 ELSE p.amount END), 0)::bigint "
        f"FROM subscriptions s JOIN plans p ON p.code = s.plan_code WHERE s.status IN {_ACTIVE}"
    )
    cancelled = await one(
        "SELECT count(*) FROM subscriptions WHERE status IN ('cancelled','completed') AND updated_at > now() - interval '30 days'"
    )
    return {
        "users_total": await one("SELECT count(*) FROM users WHERE deleted_at IS NULL"),
        "signups_7d": await one(
            "SELECT count(*) FROM users WHERE created_at > now() - interval '7 days'"
        ),
        "signups_30d": await one(
            "SELECT count(*) FROM users WHERE created_at > now() - interval '30 days'"
        ),
        "leads_total": await one("SELECT count(*) FROM leads"),
        "leads_confirmed": await one(
            "SELECT count(*) FROM leads WHERE confirmed_at IS NOT NULL AND unsubscribed_at IS NULL"
        ),
        "active_subscriptions": active,
        "mrr": mrr,
        "arr": mrr * 12,
        "arpu": mrr // active if active else 0,
        "new_subscriptions_30d": await one(
            "SELECT count(*) FROM subscriptions WHERE created_at > now() - interval '30 days' AND status IN ('active','authenticated','cancelled','completed')"
        ),
        "cancelled_30d": cancelled,
        "churn_30d_percent": round(100 * cancelled / (active + cancelled), 1)
        if (active + cancelled)
        else 0.0,
        "revenue_30d": await one(
            "SELECT COALESCE(SUM(amount - refunded_amount), 0)::bigint FROM payments WHERE status IN ('captured','partially_refunded') AND captured_at > now() - interval '30 days'"
        ),
        "refunded_30d": await one(
            "SELECT COALESCE(SUM(amount), 0)::bigint FROM refunds WHERE created_at > now() - interval '30 days'"
        ),
    }


async def signups_by_day(session: AsyncSession, days: int = 30) -> list[tuple[str, int]]:
    rows = await session.execute(
        text(
            "SELECT d::date, COALESCE(c, 0) FROM generate_series(current_date - (:n - 1) * interval '1 day', current_date, interval '1 day') d "
            "LEFT JOIN (SELECT created_at::date AS day, count(*) c FROM users GROUP BY 1) u ON u.day = d::date ORDER BY 1"
        ),
        {"n": days},
    )
    return [(str(d), int(c)) for d, c in rows]


async def top_events(
    session: AsyncSession, days: int = 7, limit: int = 15
) -> list[tuple[str, int, int]]:
    """(name, occurrences, distinct visitors or users)"""
    rows = await session.execute(
        text(
            "SELECT name, count(*), count(DISTINCT COALESCE(user_sub, anonymous_id)) FROM events "
            "WHERE ts > now() - make_interval(days => :d) GROUP BY name ORDER BY 2 DESC LIMIT :l"
        ),
        {"d": days, "l": limit},
    )
    return [(n, int(c), int(u)) for n, c, u in rows]


async def funnel(session: AsyncSession, steps: list[str], days: int = 30) -> list[tuple[str, int]]:
    """Distinct people who did each step (a person counts once per step, even if they skipped an earlier one)."""
    out = []
    for step in steps:
        n = (
            await session.execute(
                text(
                    "SELECT count(DISTINCT COALESCE(user_sub, anonymous_id)) FROM events WHERE name = :n AND ts > now() - make_interval(days => :d)"
                ),
                {"n": step, "d": days},
            )
        ).scalar_one()
        out.append((step, int(n)))
    return out
