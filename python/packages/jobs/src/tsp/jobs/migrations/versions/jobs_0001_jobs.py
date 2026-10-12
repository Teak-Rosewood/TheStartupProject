"""jobs and cron_jobs

Revision ID: jobs_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "jobs_0001"
down_revision = None
branch_labels = ("jobs",)
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("payload", JSONB, server_default="{}", nullable=False),
        sa.Column("status", sa.Text, server_default="queued", nullable=False),
        sa.Column("run_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("attempts", sa.Integer, server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer, server_default="5", nullable=False),
        sa.Column("locked_at", TS),
        sa.Column("locked_by", sa.Text),
        sa.Column("last_error", sa.Text),
        sa.Column("unique_key", sa.Text, unique=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", TS),
    )
    op.create_index("ix_jobs_status_run_at", "jobs", ["status", "run_at"])
    op.create_table(
        "cron_jobs",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("schedule", sa.Text, nullable=False),
        sa.Column("job_name", sa.Text, nullable=False),
        sa.Column("payload", JSONB, server_default="{}", nullable=False),
        sa.Column("next_run_at", TS, nullable=False),
        sa.Column("last_run_at", TS),
    )


def downgrade() -> None:
    op.drop_table("cron_jobs")
    op.drop_table("jobs")
