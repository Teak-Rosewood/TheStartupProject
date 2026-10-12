"""flags

Revision ID: flags_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "flags_0001"
down_revision = None
branch_labels = ("flags",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flags",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("description", sa.Text, server_default="", nullable=False),
        sa.Column("enabled", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("rollout_percent", sa.Integer, server_default="100", nullable=False),
        sa.Column("rules", JSONB, server_default="{}", nullable=False),
        sa.Column("client_visible", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.Text),
        sa.CheckConstraint("rollout_percent BETWEEN 0 AND 100", name="ck_flags_rollout_percent"),
    )


def downgrade() -> None:
    op.drop_table("flags")
