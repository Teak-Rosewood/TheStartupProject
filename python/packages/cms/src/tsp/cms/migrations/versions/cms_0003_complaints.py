"""complaints desk and restricted-mode windows

Revision ID: cms_0003
Revises: cms_0002

Only adds tables. Rollback drops them (complaint records are lost, content is not), so export
the complaints report first if you have live complaints.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "cms_0003"
down_revision = "cms_0002"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _ts(name: str, *, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.func.now() if default else None,
        nullable=not default,
    )


def upgrade() -> None:
    op.create_table(
        "cms_complaints",
        _id(),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("cms_items.id", ondelete="SET NULL")),
        sa.Column("complainant_name", sa.Text, nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        _ts("received_at", default=True),
        _ts("acknowledged_at"),
        _ts("ack_due_at"),
        _ts("resolve_due_at"),
        sa.Column("status", sa.Text, server_default="received", nullable=False),
        sa.Column("level", sa.Integer, server_default="1", nullable=False),
        sa.Column("assigned_to", sa.Text),
        sa.Column("resolution", sa.Text),
        sa.Column("correction_id", UUID(as_uuid=True), sa.ForeignKey("cms_corrections.id")),
        _ts("closed_at"),
        _ts("ack_warned_at"),
        _ts("resolve_warned_at"),
        sa.CheckConstraint(
            "status IN ('received', 'acknowledged', 'in_progress', 'resolved', 'rejected', 'escalated')",
            name="ck_cms_complaints_status",
        ),
        sa.CheckConstraint("level BETWEEN 1 AND 3", name="ck_cms_complaints_level"),
    )
    op.create_index("ix_cms_complaints_org", "cms_complaints", ["org_id", "received_at"])
    op.create_index("ix_cms_complaints_email", "cms_complaints", ["email"])
    op.create_index(
        "ix_cms_complaints_open",
        "cms_complaints",
        ["status"],
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.create_table(
        "cms_complaint_events",
        _id(),
        sa.Column(
            "complaint_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cms_complaints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _ts("at", default=True),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("note", sa.Text, server_default="", nullable=False),
    )
    op.create_index("ix_cms_complaint_events", "cms_complaint_events", ["complaint_id", "at"])
    op.create_table(
        "cms_windows",
        _id(),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        _ts("starts_at"),
        _ts("ends_at"),
        sa.Column("region", sa.Text),
        sa.Column("rules", JSONB, server_default="{}", nullable=False),
        sa.UniqueConstraint("org_id", "name", name="uq_cms_windows_name"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_cms_windows_order"),
    )
    op.alter_column("cms_windows", "starts_at", nullable=False)
    op.alter_column("cms_windows", "ends_at", nullable=False)


def downgrade() -> None:
    op.drop_table("cms_windows")
    op.drop_table("cms_complaint_events")
    op.drop_table("cms_complaints")
