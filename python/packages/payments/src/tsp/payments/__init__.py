"""Payments for India first: Razorpay, Stripe second, GST invoices, credits ledger."""

from pathlib import Path

from .credits import InsufficientCredits
from .invoicing import Invoicer, Seller, export_csv
from .models import (
    BillingProfile,
    CreditAccount,
    CreditEntry,
    Customer,
    Invoice,
    Payment,
    Plan,
    Refund,
    Subscription,
    Transfer,
    WebhookEvent,
)
from .plans import load_plans
from .providers import (
    InvalidSignature,
    PaymentProvider,
    ProviderError,
    RazorpayProvider,
    StripeProvider,
)
from .service import (
    Checkout,
    Entitlements,
    NotAllowed,
    Payments,
    PaymentsError,
    PlanSpec,
    UnknownPlan,
    WebhookResult,
)
from .storage import LocalInvoiceStore, S3InvoiceStore

__all__ = [
    "BillingProfile",
    "Checkout",
    "CreditAccount",
    "CreditEntry",
    "Customer",
    "Entitlements",
    "InsufficientCredits",
    "InvalidSignature",
    "Invoice",
    "Invoicer",
    "LocalInvoiceStore",
    "NotAllowed",
    "Payment",
    "PaymentProvider",
    "Payments",
    "PaymentsError",
    "Plan",
    "PlanSpec",
    "ProviderError",
    "RazorpayProvider",
    "Refund",
    "S3InvoiceStore",
    "Seller",
    "StripeProvider",
    "Subscription",
    "Transfer",
    "UnknownPlan",
    "WebhookEvent",
    "WebhookResult",
    "export_csv",
    "load_plans",
    "migrations_path",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.payments"])``."""
    return Path(__file__).parent / "migrations" / "versions"
