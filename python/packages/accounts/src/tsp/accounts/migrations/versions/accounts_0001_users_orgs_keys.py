"""users, orgs, memberships, invites, api_keys

Revision ID: accounts_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "accounts_0001"
down_revision = None
branch_labels = ("accounts",)
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("sub", sa.Text, primary_key=True),
        sa.Column("email", sa.Text),
        sa.Column("name", sa.Text),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", _TS),
        sa.Column("disabled_at", _TS),
        sa.Column("deleted_at", _TS),
        sa.Column("attribution", JSONB, server_default="{}", nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_table(
        "orgs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("plan", sa.Text, server_default="free", nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Text),
        sa.Column("deleted_at", _TS),
    )
    op.create_table(
        "memberships",
        sa.Column(
            "org_id", sa.Text, sa.ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "user_sub", sa.Text, sa.ForeignKey("users.sub", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_memberships_user_sub", "memberships", ["user_sub"])
    op.create_table(
        "invites",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("invited_by", sa.Text, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("accepted_at", _TS),
        sa.Column("revoked_at", _TS),
    )
    op.create_index("ix_invites_org_id", "invites", ["org_id"])
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("prefix", sa.Text, nullable=False, unique=True),
        sa.Column("key_hash", sa.Text, nullable=False),
        sa.Column("scopes", JSONB, server_default="[]", nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", _TS),
        sa.Column("expires_at", _TS),
        sa.Column("revoked_at", _TS),
    )
    op.create_index("ix_api_keys_org_id", "api_keys", ["org_id"])


def downgrade() -> None:
    for t in ("api_keys", "invites", "memberships", "orgs", "users"):
        op.drop_table(t)
