from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base


def _id() -> str:
    return uuid.uuid4().hex


_TS = DateTime(timezone=True)


class BillingProfile(Base):
    """Who is invoiced: legal name, GSTIN (optional for consumers), address and state."""

    __tablename__ = "billing_profiles"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    legal_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    gstin: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)
    country: Mapped[str] = mapped_column(Text, server_default="IN")
    state_code: Mapped[str | None] = mapped_column(Text)  # GST state code, e.g. "29" Karnataka
    updated_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class Plan(Base):
    """A sellable plan or credit pack. ``code`` is yours; ``provider_plan_id`` is the gateway's."""

    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # subscription | one_time | credit_pack
    amount: Mapped[int] = mapped_column(BigInteger)  # minor units, tax inclusive
    currency: Mapped[str] = mapped_column(Text, server_default="INR")
    interval: Mapped[str | None] = mapped_column(Text)  # month | year (subscriptions)
    provider_plan_id: Mapped[str | None] = mapped_column(Text)
    credits: Mapped[int] = mapped_column(
        Integer, server_default="0"
    )  # granted per credit pack / period
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class Customer(Base):
    __tablename__ = "payment_customers"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    provider_customer_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("org_id", "provider", name="uq_payment_customers_org_provider"),
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(Text, index=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"))
    provider: Mapped[str] = mapped_column(Text)
    provider_subscription_id: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text)
    current_period_end: Mapped[datetime | None] = mapped_column(_TS)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(Text, index=True)
    provider: Mapped[str] = mapped_column(Text)
    provider_payment_id: Mapped[str | None] = mapped_column(Text, unique=True)
    provider_order_id: Mapped[str | None] = mapped_column(Text, index=True)
    plan_code: Mapped[str | None] = mapped_column(Text)
    subscription_id: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[int] = mapped_column(BigInteger)
    refunded_amount: Mapped[int] = mapped_column(BigInteger, server_default="0")
    currency: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text
    )  # created | authorized | captured | failed | refunded | partially_refunded
    method: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime | None] = mapped_column(_TS)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"))
    provider_refund_id: Mapped[str] = mapped_column(Text, unique=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class Transfer(Base):
    """Marketplace payout leg: a share of a captured payment sent to a seller's linked account."""

    __tablename__ = "transfers"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"))
    seller_org_id: Mapped[str] = mapped_column(Text, index=True)
    provider_account_id: Mapped[str] = mapped_column(Text)
    provider_transfer_id: Mapped[str] = mapped_column(Text, unique=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class WebhookEvent(Base):
    """Every provider event, once. The primary key is what makes redelivery harmless."""

    __tablename__ = "webhook_events"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(_TS)
    error: Mapped[str | None] = mapped_column(Text)


class InvoiceSequence(Base):
    """Last number issued per financial year and series; the row lock makes numbering gapless."""

    __tablename__ = "invoice_sequences"

    fy: Mapped[str] = mapped_column(Text, primary_key=True)
    series: Mapped[str] = mapped_column(Text, primary_key=True)
    last: Mapped[int] = mapped_column(Integer, server_default="0")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    number: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)  # invoice | credit_note
    fy: Mapped[str] = mapped_column(Text)
    org_id: Mapped[str] = mapped_column(Text, index=True)
    payment_id: Mapped[str | None] = mapped_column(Text, index=True)
    original_invoice_id: Mapped[str | None] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(_TS)
    buyer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    seller: Mapped[dict[str, Any]] = mapped_column(JSONB)
    place_of_supply: Mapped[str] = mapped_column(Text)
    supply_type: Mapped[str] = mapped_column(Text)  # intra_state | inter_state | export
    sac: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    taxable: Mapped[int] = mapped_column(BigInteger)
    cgst: Mapped[int] = mapped_column(BigInteger)
    sgst: Mapped[int] = mapped_column(BigInteger)
    igst: Mapped[int] = mapped_column(BigInteger)
    total: Mapped[int] = mapped_column(BigInteger)
    pdf_key: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_invoices_issued_at", "issued_at"),)


class CreditAccount(Base):
    __tablename__ = "credit_accounts"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    balance: Mapped[int] = mapped_column(BigInteger, server_default="0")


class CreditEntry(Base):
    """Append-only ledger of credit movements; ``ref`` makes grants and spends idempotent."""

    __tablename__ = "credit_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text)
    delta: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    ref: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("org_id", "ref", name="uq_credit_ledger_org_ref"),
        Index("ix_credit_ledger_org_id_id", "org_id", "id"),
    )
