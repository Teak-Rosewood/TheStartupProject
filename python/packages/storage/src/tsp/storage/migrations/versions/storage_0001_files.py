"""files

Revision ID: storage_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "storage_0001"
down_revision = None
branch_labels = ("storage",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("user_sub", sa.Text, nullable=False),
        sa.Column("key", sa.Text, nullable=False, unique=True),
        sa.Column("filename", sa.Text, nullable=False),
        sa.Column("content_type", sa.Text, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, server_default="pending", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_files_org_id_status", "files", ["org_id", "status"])


def downgrade() -> None:
    op.drop_table("files")
