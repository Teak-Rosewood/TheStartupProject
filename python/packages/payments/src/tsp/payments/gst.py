"""GST arithmetic and numbering. Integers only (paise); no floats anywhere near money.

The rules encoded here are the common ones for software services sold in India. They are a
starting point to agree with your chartered accountant, not tax advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

IST = timezone(timedelta(hours=5, minutes=30))
_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

INTRA, INTER, EXPORT = "intra_state", "inter_state", "export"


def valid_gstin(gstin: str) -> bool:
    """Format and check character (mod-36 checksum) of a 15-character GSTIN."""
    g = (gstin or "").strip().upper()
    if not _GSTIN.match(g):
        return False
    total = 0
    for i, ch in enumerate(g[:14]):
        v = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return _CHARS[(36 - total % 36) % 36] == g[14]


def state_of_gstin(gstin: str) -> str:
    return gstin.strip()[:2]


def financial_year(when: datetime) -> tuple[str, str]:
    """Indian financial year (1 April to 31 March) of an instant, judged in IST: ("2026-27", "2627")."""
    t = when.astimezone(IST)
    start = t.year if t.month >= 4 else t.year - 1
    return f"{start}-{(start + 1) % 100:02d}", f"{start % 100:02d}{(start + 1) % 100:02d}"


def invoice_number(prefix: str, fy_short: str, seq: int) -> str:
    """e.g. ``INV/2627/000001``. GST allows at most 16 characters (letters, digits, / and -)."""
    number = f"{prefix}/{fy_short}/{seq:06d}"
    if len(number) > 16:
        raise ValueError(f"invoice number {number!r} exceeds 16 characters; shorten the prefix")
    return number


def supply_type(seller_state: str, buyer_country: str, buyer_state: str | None) -> str:
    """Export for a buyer outside India; otherwise intra- or inter-state by place of supply.

    For a buyer in India with no state recorded (typical consumer), the place of supply defaults
    to the seller's state, i.e. intra-state.
    """
    if buyer_country.upper() != "IN":
        return EXPORT
    return INTRA if (buyer_state or seller_state) == seller_state else INTER


@dataclass(frozen=True)
class Tax:
    supply: str
    taxable: int
    cgst: int
    sgst: int
    igst: int

    @property
    def total(self) -> int:
        return self.taxable + self.cgst + self.sgst + self.igst


def _round(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def split_inclusive(gross: int, rate_percent: Decimal | int | str, supply: str) -> Tax:
    """Split a tax-inclusive amount into taxable value and tax. ``taxable + taxes == gross`` always.

    Intra-state: CGST and SGST are equal halves; if the tax is an odd number of paise, the odd paisa
    stays in the taxable value so the two halves match (agree this rule with your CA).
    Export (with a Letter of Undertaking): zero-rated, no tax.
    """
    if gross < 0:
        raise ValueError("amount must not be negative")
    if supply == EXPORT:
        return Tax(EXPORT, gross, 0, 0, 0)
    rate = Decimal(str(rate_percent))
    tax_total = _round(Decimal(gross) * rate / (Decimal(100) + rate))
    if supply == INTER:
        return Tax(INTER, gross - tax_total, 0, 0, tax_total)
    half = tax_total // 2
    return Tax(INTRA, gross - 2 * half, half, half, 0)
