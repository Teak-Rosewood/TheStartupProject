"""Issue GST invoices and credit notes with gapless numbers per financial year."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .gst import (
    EXPORT,
    INTER,
    financial_year,
    invoice_number,
    split_inclusive,
    state_of_gstin,
    supply_type,
    valid_gstin,
)
from .models import BillingProfile, Invoice, Payment, Plan
from .pdf import render_pdf


class InvoiceStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def url(self, key: str, ttl_seconds: int) -> str: ...


@dataclass(frozen=True)
class Seller:
    """Your company on every invoice. ``state_code`` is the two-digit GST state code of your GSTIN."""

    legal_name: str
    gstin: str
    address: str
    state_code: str
    sac: str  # service accounting code for your supply; ask your CA
    gst_rate: Decimal = Decimal(18)
    invoice_prefix: str = "INV"
    credit_prefix: str = "CN"
    lut: bool = True  # Letter of Undertaking filed: exports are zero-rated

    def __post_init__(self) -> None:
        if not valid_gstin(self.gstin):
            raise ValueError("seller GSTIN is not valid")
        if state_of_gstin(self.gstin) != self.state_code:
            raise ValueError("seller state_code must match the first two digits of the GSTIN")
        invoice_number(
            self.invoice_prefix, "2627", 999999
        )  # fails fast if a prefix would break the 16-character rule
        invoice_number(self.credit_prefix, "2627", 999999)

    def party(self) -> dict[str, str]:
        return {
            "name": self.legal_name,
            "gstin": self.gstin,
            "address": self.address,
            "state_code": self.state_code,
        }


def validate_profile(p: BillingProfile) -> None:
    """Normalize and check GST details so a bad profile cannot produce an invalid invoice."""
    p.country = (p.country or "IN").upper()
    if p.gstin:
        p.gstin = p.gstin.strip().upper()
        if not valid_gstin(p.gstin):
            raise ValueError("that GSTIN is not valid")
        p.state_code = state_of_gstin(p.gstin)
    if (
        p.country == "IN"
        and p.state_code is not None
        and not (p.state_code.isdigit() and len(p.state_code) == 2)
    ):
        raise ValueError("state_code must be the two-digit GST state code")


class Invoicer:
    def __init__(self, seller: Seller, store: InvoiceStore):
        self.seller = seller
        self.store = store

    async def _next(self, session: AsyncSession, fy: str, series: str) -> int:
        await session.execute(
            text(
                "INSERT INTO invoice_sequences (fy, series, last) VALUES (:fy, :s, 0) ON CONFLICT DO NOTHING"
            ),
            {"fy": fy, "s": series},
        )
        # The UPDATE locks the row until commit, so concurrent issuers queue and numbers have no gaps.
        row = await session.execute(
            text(
                "UPDATE invoice_sequences SET last = last + 1 WHERE fy = :fy AND series = :s RETURNING last"
            ),
            {"fy": fy, "s": series},
        )
        return int(row.scalar_one())

    def _buyer(self, profile: BillingProfile | None, fallback_email: str | None) -> dict[str, Any]:
        if profile is None:
            return {
                "name": fallback_email or "Customer",
                "email": fallback_email,
                "address": "",
                "country": "IN",
            }
        return {
            "name": profile.legal_name,
            "email": profile.email,
            "gstin": profile.gstin,
            "address": profile.address,
            "country": profile.country,
            "state_code": profile.state_code,
        }

    def _tax(self, gross: int, currency: str, buyer: dict[str, Any]) -> tuple[str, Any, str]:
        supply = supply_type(
            self.seller.state_code, buyer.get("country", "IN"), buyer.get("state_code")
        )
        taxed_as = (EXPORT if self.seller.lut else INTER) if supply == EXPORT else supply
        place = (
            buyer.get("country")
            if supply == EXPORT
            else (buyer.get("state_code") or self.seller.state_code)
        )
        return supply, split_inclusive(gross, self.seller.gst_rate, taxed_as), str(place)

    async def issue_for_payment(
        self,
        session: AsyncSession,
        payment: Payment,
        plan: Plan | None,
        profile: BillingProfile | None,
        *,
        email: str | None = None,
        now: datetime | None = None,
    ) -> Invoice:
        """One invoice per captured payment; calling again returns the same invoice."""
        existing = (
            await session.execute(
                select(Invoice).where(Invoice.payment_id == payment.id, Invoice.kind == "invoice")
            )
        ).scalar_one_or_none()
        if existing:
            return existing
        issued = now or datetime.now(UTC)
        fy, short = financial_year(issued)
        buyer = self._buyer(profile, email)
        supply, tax, place = self._tax(payment.amount, payment.currency, buyer)
        number = invoice_number(
            self.seller.invoice_prefix,
            short,
            await self._next(session, fy, self.seller.invoice_prefix),
        )
        inv = Invoice(
            number=number,
            kind="invoice",
            fy=fy,
            org_id=payment.org_id,
            payment_id=payment.id,
            issued_at=issued,
            buyer=buyer,
            seller=self.seller.party(),
            place_of_supply=place,
            supply_type=supply,
            sac=self.seller.sac,
            description=plan.name if plan else "Software subscription",
            currency=payment.currency,
            taxable=tax.taxable,
            cgst=tax.cgst,
            sgst=tax.sgst,
            igst=tax.igst,
            total=tax.total,
        )
        session.add(inv)
        await session.flush()
        await self._store_pdf(inv)
        return inv

    async def issue_credit_note(
        self,
        session: AsyncSession,
        original: Invoice,
        refund_amount: int,
        *,
        now: datetime | None = None,
    ) -> Invoice:
        issued = now or datetime.now(UTC)
        fy, short = financial_year(issued)
        supply = original.supply_type
        taxed_as = (EXPORT if self.seller.lut else INTER) if supply == EXPORT else supply
        tax = split_inclusive(refund_amount, self.seller.gst_rate, taxed_as)
        number = invoice_number(
            self.seller.credit_prefix,
            short,
            await self._next(session, fy, self.seller.credit_prefix),
        )
        cn = Invoice(
            number=number,
            kind="credit_note",
            fy=fy,
            org_id=original.org_id,
            payment_id=original.payment_id,
            original_invoice_id=original.id,
            issued_at=issued,
            buyer=original.buyer,
            seller=original.seller,
            place_of_supply=original.place_of_supply,
            supply_type=supply,
            sac=original.sac,
            description=f"Credit note for {original.number}",
            currency=original.currency,
            taxable=tax.taxable,
            cgst=tax.cgst,
            sgst=tax.sgst,
            igst=tax.igst,
            total=tax.total,
        )
        session.add(cn)
        await session.flush()
        await self._store_pdf(cn, original_number=original.number)
        return cn

    def as_dict(self, inv: Invoice, original_number: str | None = None) -> dict[str, Any]:
        d = {c.name: getattr(inv, c.name) for c in Invoice.__table__.columns}
        d["issued_at"] = inv.issued_at.isoformat()
        d["original_number"] = original_number
        return d

    async def _store_pdf(self, inv: Invoice, original_number: str | None = None) -> None:
        key = f"invoices/{inv.fy}/{inv.number.replace('/', '-')}.pdf"
        await self.store.put(key, render_pdf(self.as_dict(inv, original_number)))
        inv.pdf_key = key

    async def pdf_url(self, inv: Invoice, ttl_seconds: int = 900) -> str | None:
        return await self.store.url(inv.pdf_key, ttl_seconds) if inv.pdf_key else None


async def export_csv(session: AsyncSession, fy: str) -> str:
    """Invoices and credit notes of a financial year, for your accountant (Zoho Books, Tally import)."""
    rows = (
        (
            await session.execute(
                select(Invoice).where(Invoice.fy == fy).order_by(Invoice.kind, Invoice.number)
            )
        )
        .scalars()
        .all()
    )
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "number",
            "type",
            "date",
            "buyer",
            "buyer_gstin",
            "place_of_supply",
            "supply",
            "sac",
            "currency",
            "taxable",
            "cgst",
            "sgst",
            "igst",
            "total",
        ]
    )
    sign = lambda inv: -1 if inv.kind == "credit_note" else 1  # noqa: E731 - credit notes reduce the books
    for i in rows:
        k = sign(i)
        w.writerow(
            [
                i.number,
                i.kind,
                i.issued_at.date().isoformat(),
                i.buyer.get("name", ""),
                i.buyer.get("gstin") or "",
                i.place_of_supply,
                i.supply_type,
                i.sac,
                i.currency,
            ]
            + [f"{Decimal(k * v) / 100:.2f}" for v in (i.taxable, i.cgst, i.sgst, i.igst, i.total)]
        )
    return out.getvalue()


__all__ = ["InvoiceStore", "Invoicer", "Seller", "export_csv", "validate_profile"]
