"""ai_batches: Message Batches in flight and their collected results

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "ai_batches",
        sa.Column("id", sa.Text, primary_key=True),  # the provider's batch id
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("note", sa.Text, server_default="", nullable=False),
        sa.Column("status", sa.Text, server_default="submitted", nullable=False),
        sa.Column("counts", JSONB, server_default="{}", nullable=False),
        sa.Column("results", JSONB),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("collected_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('submitted', 'collected', 'failed')", name="ck_ai_batches_status"
        ),
    )
    op.create_index("ix_ai_batches_status", "ai_batches", ["status"])


def downgrade() -> None:
    op.drop_table("ai_batches")
