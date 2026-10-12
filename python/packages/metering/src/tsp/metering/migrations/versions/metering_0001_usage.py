"""usage_records

Revision ID: metering_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "metering_0001"
down_revision = None
branch_labels = ("metering",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("api_key_id", sa.Text),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("model", sa.Text),
        sa.Column("provider", sa.Text),
        sa.Column("units_in", sa.Integer, server_default="0", nullable=False),
        sa.Column("units_out", sa.Integer, server_default="0", nullable=False),
        sa.Column("credits", sa.Integer, server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("status", sa.Text, server_default="ok", nullable=False),
        sa.Column("request_id", sa.Text),
        sa.Column("meta", JSONB, server_default="{}", nullable=False),
    )
    op.create_index("ix_usage_records_org_ts", "usage_records", ["org_id", "ts"])
    op.create_index("ix_usage_records_kind_ts", "usage_records", ["kind", "ts"])


def downgrade() -> None:
    op.drop_table("usage_records")
