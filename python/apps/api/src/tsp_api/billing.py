"""Build the payments service from environment variables (credentials come from Secrets Manager)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from tsp.payments import (
    Invoicer,
    LocalInvoiceStore,
    PaymentProvider,
    Payments,
    RazorpayProvider,
    S3InvoiceStore,
    Seller,
    StripeProvider,
    load_plans,
)


def build_payments(env: Mapping[str, str] | None = None, **hooks: Any) -> Payments | None:
    """None when no gateway is configured, so an app without payments still starts."""
    e = os.environ if env is None else env
    providers: dict[str, PaymentProvider] = {}
    if (
        e.get("RAZORPAY_KEY_ID")
        and e.get("RAZORPAY_KEY_SECRET")
        and e.get("RAZORPAY_WEBHOOK_SECRET")
    ):
        providers["razorpay"] = RazorpayProvider(
            e["RAZORPAY_KEY_ID"], e["RAZORPAY_KEY_SECRET"], e["RAZORPAY_WEBHOOK_SECRET"]
        )
    if e.get("STRIPE_SECRET_KEY") and e.get("STRIPE_WEBHOOK_SECRET"):
        providers["stripe"] = StripeProvider(
            e["STRIPE_SECRET_KEY"],
            e["STRIPE_WEBHOOK_SECRET"],
            success_url=e.get("STRIPE_SUCCESS_URL", "/billing/success"),
            cancel_url=e.get("STRIPE_CANCEL_URL", "/billing/cancelled"),
        )
    if not providers:
        return None

    invoicer = None
    if all(
        e.get(k)
        for k in (
            "SELLER_LEGAL_NAME",
            "SELLER_GSTIN",
            "SELLER_ADDRESS",
            "SELLER_STATE_CODE",
            "SELLER_SAC",
        )
    ):
        store = (
            S3InvoiceStore(e["INVOICE_BUCKET"], e.get("AWS_REGION"))
            if e.get("INVOICE_BUCKET")
            else LocalInvoiceStore("/tmp/tsp-invoices")
        )
        seller = Seller(
            e["SELLER_LEGAL_NAME"],
            e["SELLER_GSTIN"],
            e["SELLER_ADDRESS"],
            e["SELLER_STATE_CODE"],
            e["SELLER_SAC"],
            gst_rate=Decimal(e.get("SELLER_GST_RATE", "18")),
            lut=e.get("SELLER_LUT", "1") == "1",
        )
        invoicer = Invoicer(seller, store)
    default = e.get("DEFAULT_PAYMENT_PROVIDER") or next(iter(providers))
    return Payments(
        providers, default_provider=default, invoicer=invoicer, free_features={"seats": 1}
    )


def plans_path(env: Mapping[str, str] | None = None) -> Path | None:
    e = os.environ if env is None else env
    p = Path(e.get("TSP_PLANS_FILE", "plans.toml"))
    return p if p.exists() else None


async def sync_plans_on_start(payments: Payments, database_url: str, path: Path) -> int:
    from tsp.db import Database

    plans = load_plans(path)
    db = Database(database_url)
    try:
        async with db.session() as s:
            await payments.sync_plans(s, plans)
    finally:
        await db.dispose()
    return len(plans)
