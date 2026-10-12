"""pipeline runs, steps, prompts and trace

Revision ID: pipeline_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "pipeline_0001"
down_revision = None
branch_labels = ("pipeline",)
depends_on = None


def _id() -> sa.Column:
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def upgrade() -> None:
    op.create_table(
        "pl_runs",
        _id(),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("workflow", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("input", JSONB, nullable=False),
        sa.Column("output", JSONB),
        sa.Column("status", sa.Text, server_default="queued", nullable=False),
        sa.Column("budget_usd", sa.Numeric),
        sa.Column("spent_usd", sa.Numeric, server_default="0", nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text),
        sa.Column(
            "parent_run_id", UUID(as_uuid=True), sa.ForeignKey("pl_runs.id", ondelete="SET NULL")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')",
            name="ck_pl_runs_status",
        ),
    )
    op.create_index("ix_pl_runs_org_started", "pl_runs", ["org_id", "started_at"])
    op.create_table(
        "pl_steps",
        _id(),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pl_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("attempt", sa.Integer, server_default="1", nullable=False),
        sa.Column("status", sa.Text, server_default="queued", nullable=False),
        sa.Column("input", JSONB),
        sa.Column("output", JSONB),
        sa.Column("model", sa.Text),
        sa.Column("prompt_key", sa.Text),
        sa.Column("prompt_version", sa.Integer),
        sa.Column("tokens_in", sa.Integer, server_default="0", nullable=False),
        sa.Column("tokens_out", sa.Integer, server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric, server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text),
        sa.UniqueConstraint("run_id", "name", "attempt", name="uq_pl_steps_attempt"),
    )
    op.create_table(
        "pl_prompts",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("model_hint", sa.Text),
        sa.Column("params", JSONB, server_default="{}", nullable=False),
        sa.Column("created_by", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("notes", sa.Text),
    )
    op.create_table(
        "pl_prompt_active",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("version", sa.Integer, nullable=False),
    )
    op.create_table(
        "pl_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pl_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step", sa.Text),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("data", JSONB, server_default="{}", nullable=False),
    )
    op.create_index("ix_pl_events_run", "pl_events", ["run_id", "id"])


def downgrade() -> None:
    for table in ("pl_events", "pl_prompt_active", "pl_prompts", "pl_steps", "pl_runs"):
        op.drop_table(table)
