"""paywall tables

Revision ID: paywall_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "paywall_0001"
down_revision = None
branch_labels = ("paywall",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pw_policies",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("free_items", sa.Integer, nullable=False),
        sa.Column("window_days", sa.Integer, nullable=False),
        sa.Column("wall", sa.Text, server_default="hard", nullable=False),
        sa.Column("applies_to", JSONB, server_default="[]", nullable=False),
        sa.Column("require_account_after", sa.Integer),
        sa.Column("active", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.CheckConstraint("wall IN ('hard','soft')", name="ck_pw_policies_wall"),
        sa.CheckConstraint("free_items >= 0 AND window_days >= 1", name="ck_pw_policies_numbers"),
    )
    op.create_index("ix_pw_policies_org", "pw_policies", ["org_id", "active"])
    op.create_table(
        "pw_reads",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("user_id", sa.Text),
        sa.Column("meter_id", sa.Text),
        sa.Column("item_id", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pw_reads_user", "pw_reads", ["org_id", "user_id", "at"])
    op.create_index("ix_pw_reads_meter", "pw_reads", ["org_id", "meter_id", "at"])
    op.create_table(
        "pw_gifts",
        sa.Column("token_hash", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("item_id", sa.Text, nullable=False),
        sa.Column("giver_user_id", sa.Text, nullable=False),
        sa.Column("max_uses", sa.Integer, nullable=False),
        sa.Column("uses", sa.Integer, server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pw_gifts_giver", "pw_gifts", ["giver_user_id"])
    op.create_table(
        "pw_credits",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, primary_key=True),
        sa.Column("balance", sa.Integer, server_default="0", nullable=False),
        sa.CheckConstraint("balance >= 0", name="ck_pw_credits_balance"),
    )
    op.create_table(
        "pw_unlocks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("subject", sa.Text, nullable=False),  # "u:<user id>" or "m:<meter id>"
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("item_id", sa.Text, nullable=False),
        sa.Column("via", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("subject", "org_id", "item_id", name="uq_pw_unlocks"),
        sa.CheckConstraint("via IN ('credit','gift','subscription')", name="ck_pw_unlocks_via"),
    )


def downgrade() -> None:
    for t in ("pw_unlocks", "pw_credits", "pw_gifts", "pw_reads", "pw_policies"):
        op.drop_table(t)
