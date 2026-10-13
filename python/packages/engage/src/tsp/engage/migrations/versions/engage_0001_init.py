"""polls, responses, receipts, streaks, points, badges, onboarding

Revision ID: engage_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "engage_0001"
down_revision = None
branch_labels = ("engage",)
depends_on = None


def _ts(name: str, *, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.func.now() if default else None,
        nullable=not default,
    )


def upgrade() -> None:
    op.create_table(
        "en_polls",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("item_id", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("axes", JSONB, nullable=False),
        _ts("opens_at"),
        _ts("closes_at"),
        sa.CheckConstraint("kind IN ('scale1d', 'scale2d')", name="ck_en_polls_kind"),
    )
    op.create_index("ix_en_polls_item", "en_polls", ["org_id", "item_id"])
    op.create_table(
        "en_responses",
        sa.Column(
            "poll_id",
            UUID(as_uuid=True),
            sa.ForeignKey("en_polls.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("phase", sa.Text, primary_key=True),
        sa.Column("x", sa.Float, nullable=False),
        sa.Column("y", sa.Float),
        _ts("at", default=True),
        sa.CheckConstraint("phase IN ('before', 'after')", name="ck_en_responses_phase"),
    )
    op.create_index("ix_en_responses_user", "en_responses", ["user_id"])
    op.create_table(
        "en_receipts",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("item_id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("version_id", sa.Text),
        _ts("started_at", default=True),
        _ts("completed_at"),
        sa.Column("expected_seconds", sa.Integer),
        sa.Column("dwell_seconds", sa.Integer, server_default="0", nullable=False),
        sa.Column("max_scroll", sa.Float, server_default="0", nullable=False),
        sa.Column("reached_end", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("served_full", sa.Boolean, server_default=sa.true(), nullable=False),
    )
    op.create_table(
        "en_streaks",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, primary_key=True),
        sa.Column("period", sa.Text, primary_key=True, server_default="week"),
        sa.Column("current", sa.Integer, server_default="0", nullable=False),
        sa.Column("best", sa.Integer, server_default="0", nullable=False),
        sa.Column("last_period_key", sa.Text),
        sa.Column("progress_key", sa.Text),
        sa.Column("progress_count", sa.Integer, server_default="0", nullable=False),
    )
    op.create_table(
        "en_points",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("points", sa.Integer, nullable=False),
        sa.Column("ref_kind", sa.Text, server_default="", nullable=False),
        sa.Column("ref_id", sa.Text, server_default="", nullable=False),
        _ts("at", default=True),
        # an award is idempotent: the same reason for the same thing is paid once
        sa.UniqueConstraint(
            "user_id", "org_id", "reason", "ref_kind", "ref_id", name="uq_en_points_ref"
        ),
    )
    op.create_index("ix_en_points_user", "en_points", ["user_id", "org_id"])
    op.create_table(
        "en_badges",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, primary_key=True),
        sa.Column("badge", sa.Text, primary_key=True),
        _ts("at", default=True),
    )
    op.create_table(
        "en_onboarding",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, primary_key=True),
        sa.Column("step", sa.Text, primary_key=True),
        _ts("completed_at", default=True),
    )


def downgrade() -> None:
    for t in (
        "en_onboarding",
        "en_badges",
        "en_points",
        "en_streaks",
        "en_receipts",
        "en_responses",
        "en_polls",
    ):
        op.drop_table(t)
