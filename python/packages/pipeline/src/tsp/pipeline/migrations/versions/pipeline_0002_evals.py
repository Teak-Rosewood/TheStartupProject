"""pipeline eval runs

Revision ID: pipeline_0002
Revises: pipeline_0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "pipeline_0002"
down_revision = "pipeline_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pl_eval_runs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("suite", sa.Text, nullable=False),
        sa.Column("prompt_key", sa.Text, nullable=False),
        sa.Column("prompt_version", sa.Integer, nullable=False),
        sa.Column("model", sa.Text),
        sa.Column("scorer", sa.Text, nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("n_cases", sa.Integer, nullable=False),
        sa.Column("n_errors", sa.Integer, server_default="0", nullable=False),
        sa.Column("results", JSONB, nullable=False),
        sa.Column("baseline_version", sa.Integer),
        sa.Column("baseline_score", sa.Float),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("reasons", JSONB, server_default="[]", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_pl_eval_runs_suite", "pl_eval_runs", ["suite", "created_at"])


def downgrade() -> None:
    op.drop_table("pl_eval_runs")
