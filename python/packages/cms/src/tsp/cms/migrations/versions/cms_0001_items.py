"""cms items, versions, variants, checks, disclosure templates

Revision ID: cms_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "cms_0001"
down_revision = None
branch_labels = ("cms",)
depends_on = None


def _id() -> sa.Column:
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _ts(name: str, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.func.now() if default else None,
        nullable=not default,
    )


def upgrade() -> None:
    op.create_table(
        "cms_items",
        _id(),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("primary_lang", sa.Text, server_default="en", nullable=False),
        sa.Column("state", sa.Text, server_default="draft", nullable=False),
        sa.Column("current_version_id", UUID(as_uuid=True)),
        sa.Column("published_version_id", UUID(as_uuid=True)),
        _ts("publish_at"),
        _ts("published_at"),
        sa.Column("lifecycle", sa.Text, server_default="open", nullable=False),
        _ts("review_due_at"),
        _ts("frozen_at"),
        sa.Column("frozen_reason", sa.Text),
        sa.Column("created_by", sa.Text),
        _ts("created_at", default=True),
        sa.Column("attrs", JSONB, server_default="{}", nullable=False),
        sa.UniqueConstraint("org_id", "slug", name="uq_cms_items_org_slug"),
        sa.CheckConstraint(
            "state IN ('draft', 'in_review', 'approved', 'scheduled', 'published', 'unpublished')",
            name="ck_cms_items_state",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('open', 'living', 'frozen')", name="ck_cms_items_lifecycle"
        ),
    )
    op.create_index("ix_cms_items_due", "cms_items", ["state", "publish_at"])
    op.create_table(
        "cms_versions",
        _id(),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cms_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("lang", sa.Text, nullable=False),
        sa.Column("title", sa.Text, server_default="", nullable=False),
        sa.Column("body", JSONB, server_default="[]", nullable=False),
        sa.Column("summary", sa.Text, server_default="", nullable=False),
        sa.Column("disclosures", JSONB, server_default="[]", nullable=False),
        sa.Column("author_kind", sa.Text, server_default="human", nullable=False),
        sa.Column("ai_run_id", UUID(as_uuid=True)),
        sa.Column("created_by", sa.Text),
        _ts("created_at", default=True),
        sa.Column("change_note", sa.Text, server_default="", nullable=False),
        sa.UniqueConstraint("item_id", "number", name="uq_cms_versions_number"),
        sa.CheckConstraint(
            "author_kind IN ('human', 'ai', 'mixed')", name="ck_cms_versions_author"
        ),
    )
    op.create_foreign_key(
        "fk_cms_items_current", "cms_items", "cms_versions", ["current_version_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_cms_items_published", "cms_items", "cms_versions", ["published_version_id"], ["id"]
    )
    op.create_table(
        "cms_variants",
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cms_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("lang", sa.Text, primary_key=True),
        sa.Column(
            "version_id", UUID(as_uuid=True), sa.ForeignKey("cms_versions.id"), nullable=False
        ),
        sa.Column("state", sa.Text, server_default="draft", nullable=False),
    )
    op.create_table(
        "cms_check_results",
        _id(),
        sa.Column(
            "version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cms_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check", sa.Text, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("blocking", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("details", JSONB, server_default="{}", nullable=False),
        _ts("ran_at", default=True),
    )
    op.create_index("ix_cms_check_results_version_id", "cms_check_results", ["version_id"])
    op.create_table(
        "cms_disclosure_templates",
        sa.Column("org_id", sa.Text, primary_key=True),
        sa.Column("type", sa.Text, primary_key=True),
        sa.Column("lang", sa.Text, primary_key=True),
        sa.Column("text", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_constraint("fk_cms_items_published", "cms_items", type_="foreignkey")
    op.drop_constraint("fk_cms_items_current", "cms_items", type_="foreignkey")
    for table in (
        "cms_disclosure_templates",
        "cms_check_results",
        "cms_variants",
        "cms_versions",
        "cms_items",
    ):
        op.drop_table(table)
