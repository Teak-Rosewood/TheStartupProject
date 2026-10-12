"""jobs.queue

Adds a queue name so a separate worker (for example the browser render worker) can claim only
its own jobs. Additive: existing rows and old workers keep working on the ``default`` queue.

Revision ID: jobs_0002
Revises: jobs_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "jobs_0002"
down_revision = "jobs_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("queue", sa.Text, server_default="default", nullable=False))
    op.create_index("ix_jobs_queue_status_run_at", "jobs", ["queue", "status", "run_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_queue_status_run_at", table_name="jobs")
    op.drop_column("jobs", "queue")
