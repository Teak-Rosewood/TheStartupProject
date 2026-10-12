"""leads, events, referral codes, referrals

Revision ID: growth_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "growth_0001"
down_revision = None
branch_labels = ("growth",)
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("source", sa.Text),
        sa.Column("attribution", JSONB, server_default="{}", nullable=False),
        sa.Column("consent_text", sa.Text, nullable=False),
        sa.Column("consent_ip_hash", sa.Text),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("confirmed_at", TS),
        sa.Column("unsubscribed_at", TS),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("anonymous_id", sa.Text, nullable=False),
        sa.Column("user_sub", sa.Text),
        sa.Column("org_id", sa.Text),
        sa.Column("path", sa.Text),
        sa.Column("properties", JSONB, server_default="{}", nullable=False),
    )
    op.create_index("ix_events_name_ts", "events", ["name", "ts"])
    op.create_index("ix_events_user_sub_ts", "events", ["user_sub", "ts"])
    op.create_index("ix_events_anonymous_id", "events", ["anonymous_id"])
    op.create_table(
        "referral_codes",
        sa.Column("code", sa.Text, primary_key=True),
        sa.Column("owner_sub", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "referrals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("referred_sub", sa.Text, nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("referred_sub", name="uq_referrals_referred_sub"),
    )
    op.create_index("ix_referrals_code", "referrals", ["code"])


def downgrade() -> None:
    for t in ("referrals", "referral_codes", "events", "leads"):
        op.drop_table(t)
