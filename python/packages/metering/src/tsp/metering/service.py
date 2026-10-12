from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UsageRecord


@dataclass(frozen=True)
class Rates:
    """Credits charged per 1,000 tokens. Output usually costs more than input; set them from your
    provider prices plus margin (docs/startup/03-metrics.md: know your cost per request)."""

    in_per_1k: float = 1.0
    out_per_1k: float = 3.0
    min_charge: int = 1  # every successful call costs at least this

    def __post_init__(self) -> None:
        if min(self.in_per_1k, self.out_per_1k) < 0 or self.min_charge < 0:
            raise ValueError("rates cannot be negative")


def price_credits(tokens_in: int, tokens_out: int, rates: Rates) -> int:
    """Whole credits, rounded up so fractions never become free usage."""
    raw = tokens_in / 1000 * rates.in_per_1k + tokens_out / 1000 * rates.out_per_1k
    return max(rates.min_charge, math.ceil(raw))


def estimate_tokens(text_: str) -> int:
    """Rough count (about 4 characters per token) for providers that report no usage and for streams."""
    return max(1, math.ceil(len(text_) / 4)) if text_ else 0


async def record_usage(
    session: AsyncSession,
    *,
    org_id: str,
    kind: str,
    api_key_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    units_in: int = 0,
    units_out: int = 0,
    credits: int = 0,
    latency_ms: int | None = None,
    status: str = "ok",
    request_id: str | None = None,
    **meta: Any,
) -> None:
    session.add(
        UsageRecord(
            org_id=org_id,
            api_key_id=api_key_id,
            kind=kind,
            model=model,
            provider=provider,
            units_in=units_in,
            units_out=units_out,
            credits=credits,
            latency_ms=latency_ms,
            status=status,
            request_id=request_id,
            meta=meta,
        )
    )
    await session.flush()


async def summary(session: AsyncSession, org_id: str, days: int = 30) -> dict[str, Any]:
    days = max(1, min(days, 366))
    totals = (
        await session.execute(
            text(
                "SELECT count(*), COALESCE(sum(units_in),0), COALESCE(sum(units_out),0), COALESCE(sum(credits),0) FROM usage_records "
                "WHERE org_id = :o AND ts > now() - make_interval(days => :d) AND status = 'ok'"
            ),
            {"o": org_id, "d": days},
        )
    ).one()
    by_model = (
        await session.execute(
            text(
                "SELECT COALESCE(model,'unknown'), count(*), sum(credits) FROM usage_records WHERE org_id = :o AND ts > now() - make_interval(days => :d) "
                "AND status = 'ok' GROUP BY 1 ORDER BY 3 DESC LIMIT 20"
            ),
            {"o": org_id, "d": days},
        )
    ).all()
    by_day = (
        await session.execute(
            text(
                "SELECT ts::date, count(*), sum(credits) FROM usage_records WHERE org_id = :o AND ts > now() - make_interval(days => :d) "
                "AND status = 'ok' GROUP BY 1 ORDER BY 1"
            ),
            {"o": org_id, "d": days},
        )
    ).all()
    return {
        "days": days,
        "requests": int(totals[0]),
        "tokens_in": int(totals[1]),
        "tokens_out": int(totals[2]),
        "credits": int(totals[3]),
        "by_model": [{"model": m, "requests": int(n), "credits": int(c)} for m, n, c in by_model],
        "by_day": [{"day": str(d), "requests": int(n), "credits": int(c)} for d, n, c in by_day],
    }
