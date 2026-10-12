"""emails, suppressions, preferences, device tokens, notifications

Revision ID: notify_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "notify_0001"
down_revision = None
branch_labels = ("notify",)
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "emails",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("to_addr", sa.Text, nullable=False),
        sa.Column("template", sa.Text, nullable=False),
        sa.Column("context", JSONB, server_default="{}", nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("status", sa.Text, server_default="queued", nullable=False),
        sa.Column("provider_message_id", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", TS),
    )
    op.create_index("ix_emails_to_addr", "emails", ["to_addr"])
    op.create_table(
        "email_suppressions",
        sa.Column("email", sa.Text, primary_key=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "email_preferences",
        sa.Column("email", sa.Text, primary_key=True),
        sa.Column("marketing", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "device_tokens",
        sa.Column("token", sa.Text, primary_key=True),
        sa.Column("user_sub", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_device_tokens_user_sub", "device_tokens", ["user_sub"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_sub", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, server_default="", nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", TS),
    )
    op.create_index("ix_notifications_user_sub_id", "notifications", ["user_sub", "id"])


def downgrade() -> None:
    for t in (
        "notifications",
        "device_tokens",
        "email_preferences",
        "email_suppressions",
        "emails",
    ):
        op.drop_table(t)
