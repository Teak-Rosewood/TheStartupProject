"""sources

Revision ID: sources_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "sources_0001"
down_revision = None
branch_labels = ("sources",)
depends_on = None

_NOW = sa.func.now()


def upgrade() -> None:
    op.create_table(
        "src_sources",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text),
        sa.Column("key", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("tier", sa.Text),
        sa.Column("country", sa.Text),
        sa.Column("languages", sa.ARRAY(sa.Text), server_default="{}", nullable=False),
        sa.Column("licence", sa.Text),
        sa.Column("terms_url", sa.Text),
        sa.Column("robots_ok", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("terms_checked_at", sa.DateTime(timezone=True)),
        sa.Column("allowed_uses", sa.ARRAY(sa.Text), server_default="{}", nullable=False),
        sa.Column("strategies", JSONB, server_default="[]", nullable=False),
        sa.Column("schedule", sa.Text),
        sa.Column("rate_limit_rps", sa.Float, server_default="1.0", nullable=False),
        sa.Column("owner", sa.Text),
        sa.Column("enabled", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("paid_cap_monthly", sa.Numeric(12, 4)),
        sa.Column("notes", sa.Text),
        sa.CheckConstraint("rate_limit_rps > 0", name="ck_src_sources_rps"),
    )
    op.create_table(
        "src_cursors",
        sa.Column(
            "source_id",
            sa.Integer,
            sa.ForeignKey("src_sources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("stream", sa.Text, primary_key=True),
        sa.Column("value", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_table(
        "src_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            sa.Integer,
            sa.ForeignKey("src_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stream", sa.Text, nullable=False),
        sa.Column("strategy_used", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("items_new", sa.Integer, server_default="0", nullable=False),
        sa.Column("items_updated", sa.Integer, server_default="0", nullable=False),
        sa.Column("errors", sa.Integer, server_default="0", nullable=False),
        sa.Column("cost", sa.Numeric(12, 4), server_default="0", nullable=False),
        sa.Column("manifest_uri", sa.Text),
        sa.Column("error_summary", sa.Text),
        sa.CheckConstraint("status IN ('ok','partial','failed')", name="ck_src_runs_status"),
    )
    op.create_index("ix_src_runs_source_started", "src_runs", ["source_id", "started_at"])
    op.create_table(
        "src_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            sa.Integer,
            sa.ForeignKey("src_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stream", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("checksum", sa.Text, nullable=False),
        sa.Column("raw_uri", sa.Text),
        sa.Column("content_type", sa.Text),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("metadata", JSONB, server_default="{}", nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_src_items_source_external"),
    )
    op.create_table(
        "src_checks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            sa.Integer,
            sa.ForeignKey("src_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("robots_ok", sa.Boolean),
        sa.Column("terms_hash", sa.Text),
        sa.Column("reachable", sa.Boolean, nullable=False),
        sa.Column("notes", sa.Text),
    )
    op.create_index("ix_src_checks_source_checked", "src_checks", ["source_id", "checked_at"])


def downgrade() -> None:
    for t in ("src_checks", "src_items", "src_runs", "src_cursors", "src_sources"):
        op.drop_table(t)
