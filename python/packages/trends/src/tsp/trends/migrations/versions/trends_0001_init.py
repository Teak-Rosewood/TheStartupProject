"""trends

Revision ID: trends_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "trends_0001"
down_revision = None
branch_labels = ("trends",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tr_topics",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("keywords", sa.ARRAY(sa.Text), server_default="{}", nullable=False),
        sa.Column("langs", sa.ARRAY(sa.Text), server_default="{}", nullable=False),
        sa.Column("regions", sa.ARRAY(sa.Text), server_default="{}", nullable=False),
        sa.Column("active", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.UniqueConstraint("org_id", "key", name="uq_tr_topics_org_key"),
    )
    op.create_table(
        "tr_signals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("topic_id", sa.Text, sa.ForeignKey("tr_topics.id", ondelete="CASCADE")),
        sa.Column("signal", sa.Text, nullable=False),
        sa.Column("region", sa.Text, server_default="", nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Numeric, nullable=False),
        sa.Column("baseline", sa.Numeric),
        sa.Column("attrs", JSONB, server_default="{}", nullable=False),
    )
    op.create_index("ix_tr_signals_org_at", "tr_signals", ["org_id", "at"])
    op.create_table(
        "tr_candidates",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("topic_id", sa.Text, sa.ForeignKey("tr_topics.id", ondelete="SET NULL")),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("components", JSONB, server_default="{}", nullable=False),
        sa.Column("status", sa.Text, server_default="new", nullable=False),
        sa.Column("picked_by", sa.Text),
        sa.Column("note", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "day", "label", name="uq_tr_candidates_org_day_label"),
        sa.CheckConstraint(
            "status IN ('new','picked','dismissed')", name="ck_tr_candidates_status"
        ),
    )
    op.create_index("ix_tr_candidates_org_day", "tr_candidates", ["org_id", "day"])


def downgrade() -> None:
    for t in ("tr_candidates", "tr_signals", "tr_topics"):
        op.drop_table(t)
