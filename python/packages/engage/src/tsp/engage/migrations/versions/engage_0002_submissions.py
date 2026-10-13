"""structured submissions (a side and a take) with moderation and appeals

Revision ID: engage_0002
Revises: engage_0001

Adds one table. Rollback drops it, and the submissions with it: export first if you have live ones.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "engage_0002"
down_revision = "engage_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "en_submissions",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("item_id", sa.Text, nullable=False),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("author_name", sa.Text, server_default="", nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.Text, server_default="pending", nullable=False),
        sa.Column("needs_review", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("reasons", JSONB, server_default="[]", nullable=False),
        sa.Column("scores", JSONB, server_default="{}", nullable=False),
        sa.Column("points_awarded", sa.Integer, server_default="0", nullable=False),
        sa.Column("run_id", UUID(as_uuid=True)),
        sa.Column("appeal_complaint_id", UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("item_id", "user_id", "side", name="uq_en_submissions_take"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'appealed')",
            name="ck_en_submissions_status",
        ),
    )
    op.create_index(
        "ix_en_submissions_item", "en_submissions", ["org_id", "item_id", "side", "status"]
    )
    op.create_index("ix_en_submissions_user", "en_submissions", ["user_id"])


def downgrade() -> None:
    op.drop_table("en_submissions")
