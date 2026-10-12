"""consents and data_requests

Revision ID: compliance_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "compliance_0001"
down_revision = None
branch_labels = ("compliance",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consents",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_sub", sa.Text, nullable=False),
        sa.Column("document", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column(
            "accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ip", sa.Text),
        sa.Column("user_agent", sa.Text),
        sa.Column("source", sa.Text, server_default="app", nullable=False),
    )
    op.create_index(
        "ix_consents_user_document_accepted", "consents", ["user_sub", "document", "accepted_at"]
    )
    op.create_table(
        "data_requests",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("user_sub", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("result_key", sa.Text),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_data_requests_user_sub", "data_requests", ["user_sub"])


def downgrade() -> None:
    op.drop_table("data_requests")
    op.drop_table("consents")
