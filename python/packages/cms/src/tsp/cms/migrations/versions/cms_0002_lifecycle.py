"""corrections log and review cadence

Revision ID: cms_0002
Revises: cms_0001

Adds two tables and changes nothing that exists, so the previous release keeps working while this
one rolls out. Rollback drops the two tables (the correction log is lost, not the content).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "cms_0002"
down_revision = "cms_0001"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def upgrade() -> None:
    op.create_table(
        "cms_corrections",
        _id(),
        sa.Column(
            "seq", sa.BigInteger, sa.Identity(), nullable=False
        ),  # stable order within one instant
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cms_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_from", UUID(as_uuid=True), sa.ForeignKey("cms_versions.id")),
        sa.Column("version_to", UUID(as_uuid=True), sa.ForeignKey("cms_versions.id")),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("public_note", sa.Text, nullable=False),
        sa.Column("internal_note", sa.Text, server_default="", nullable=False),
        sa.Column("complaint_id", UUID(as_uuid=True)),  # filled by the complaints desk (K4-C)
        sa.Column("created_by", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('correction', 'clarification', 'update')", name="ck_cms_corrections_kind"
        ),
        sa.CheckConstraint("length(trim(public_note)) > 0", name="ck_cms_corrections_note"),
    )
    op.create_index("ix_cms_corrections_item", "cms_corrections", ["item_id", "created_at"])
    op.create_index("ix_cms_corrections_org", "cms_corrections", ["org_id", "created_at"])
    op.create_table(
        "cms_reviews",
        _id(),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cms_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision", sa.Text),
        sa.Column("decided_by", sa.Text),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('living', 'frozen')", name="ck_cms_reviews_decision"
        ),
    )
    # One open review per item: the daily job can run twice without creating a second one.
    op.create_index(
        "uq_cms_reviews_open",
        "cms_reviews",
        ["item_id"],
        unique=True,
        postgresql_where=sa.text("decided_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("cms_reviews")
    op.drop_table("cms_corrections")
