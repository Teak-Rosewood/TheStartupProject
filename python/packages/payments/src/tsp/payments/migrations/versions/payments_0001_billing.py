"""payments, invoices, credits

Revision ID: payments_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "payments_0001"
down_revision = None
branch_labels = ("payments",)
depends_on = None

TS = sa.DateTime(timezone=True)
NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "billing_profiles",
        sa.Column("org_id", sa.Text, primary_key=True),
        sa.Column("legal_name", sa.Text, nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("gstin", sa.Text),
        sa.Column("address", sa.Text, nullable=False),
        sa.Column("country", sa.Text, server_default="IN", nullable=False),
        sa.Column("state_code", sa.Text),
        sa.Column("updated_at", TS, server_default=NOW, nullable=False),
    )
    op.create_table(
        "plans",
        sa.Column("code", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.Text, server_default="INR", nullable=False),
        sa.Column("interval", sa.Text),
        sa.Column("provider_plan_id", sa.Text),
        sa.Column("credits", sa.Integer, server_default="0", nullable=False),
        sa.Column("features", JSONB, server_default="{}", nullable=False),
        sa.Column("active", sa.Boolean, server_default=sa.true(), nullable=False),
    )
    op.create_table(
        "payment_customers",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("provider_customer_id", sa.Text, nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.UniqueConstraint("org_id", "provider", name="uq_payment_customers_org_provider"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("plan_code", sa.Text, sa.ForeignKey("plans.code"), nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("provider_subscription_id", sa.Text, nullable=False, unique=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("current_period_end", TS),
        sa.Column("cancel_at_period_end", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.Column("updated_at", TS, server_default=NOW, nullable=False),
    )
    op.create_index("ix_subscriptions_org_id", "subscriptions", ["org_id"])
    op.create_table(
        "payments",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("provider_payment_id", sa.Text, unique=True),
        sa.Column("provider_order_id", sa.Text),
        sa.Column("plan_code", sa.Text),
        sa.Column("subscription_id", sa.Text),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("refunded_amount", sa.BigInteger, server_default="0", nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("method", sa.Text),
        sa.Column("failure_reason", sa.Text),
        sa.Column("captured_at", TS),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_index("ix_payments_org_id", "payments", ["org_id"])
    op.create_index("ix_payments_provider_order_id", "payments", ["provider_order_id"])
    op.create_table(
        "refunds",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payment_id", sa.Text, sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("provider_refund_id", sa.Text, nullable=False, unique=True),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_table(
        "transfers",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payment_id", sa.Text, sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("seller_org_id", sa.Text, nullable=False),
        sa.Column("provider_account_id", sa.Text, nullable=False),
        sa.Column("provider_transfer_id", sa.Text, nullable=False, unique=True),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_index("ix_transfers_seller_org_id", "transfers", ["seller_org_id"])
    op.create_table(
        "webhook_events",
        sa.Column("provider", sa.Text, primary_key=True),
        sa.Column("event_id", sa.Text, primary_key=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("received_at", TS, server_default=NOW, nullable=False),
        sa.Column("processed_at", TS),
        sa.Column("error", sa.Text),
    )
    op.create_table(
        "invoice_sequences",
        sa.Column("fy", sa.Text, primary_key=True),
        sa.Column("series", sa.Text, primary_key=True),
        sa.Column("last", sa.Integer, server_default="0", nullable=False),
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("number", sa.Text, nullable=False, unique=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("fy", sa.Text, nullable=False),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("payment_id", sa.Text),
        sa.Column("original_invoice_id", sa.Text),
        sa.Column("issued_at", TS, nullable=False),
        sa.Column("buyer", JSONB, nullable=False),
        sa.Column("seller", JSONB, nullable=False),
        sa.Column("place_of_supply", sa.Text, nullable=False),
        sa.Column("supply_type", sa.Text, nullable=False),
        sa.Column("sac", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("taxable", sa.BigInteger, nullable=False),
        sa.Column("cgst", sa.BigInteger, nullable=False),
        sa.Column("sgst", sa.BigInteger, nullable=False),
        sa.Column("igst", sa.BigInteger, nullable=False),
        sa.Column("total", sa.BigInteger, nullable=False),
        sa.Column("pdf_key", sa.Text),
    )
    op.create_index("ix_invoices_org_id", "invoices", ["org_id"])
    op.create_index("ix_invoices_payment_id", "invoices", ["payment_id"])
    op.create_index("ix_invoices_issued_at", "invoices", ["issued_at"])
    op.create_table(
        "credit_accounts",
        sa.Column("org_id", sa.Text, primary_key=True),
        sa.Column("balance", sa.BigInteger, server_default="0", nullable=False),
        sa.CheckConstraint("balance >= 0", name="ck_credit_accounts_non_negative"),
    )
    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("delta", sa.BigInteger, nullable=False),
        sa.Column("balance_after", sa.BigInteger, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("ref", sa.Text),
        sa.Column("meta", JSONB, server_default="{}", nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.UniqueConstraint("org_id", "ref", name="uq_credit_ledger_org_ref"),
    )
    op.create_index("ix_credit_ledger_org_id_id", "credit_ledger", ["org_id", "id"])


def downgrade() -> None:
    for t in (
        "credit_ledger",
        "credit_accounts",
        "invoices",
        "invoice_sequences",
        "webhook_events",
        "transfers",
        "refunds",
        "payments",
        "subscriptions",
        "payment_customers",
        "plans",
        "billing_profiles",
    ):
        op.drop_table(t)
