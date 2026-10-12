"""crawl

Revision ID: crawl_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "crawl_0001"
down_revision = None
branch_labels = ("crawl",)
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crawl_sources",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("collection_id", sa.Text, nullable=False),
        sa.Column("seed_url", sa.Text, nullable=False),
        sa.Column("sitemap_url", sa.Text),
        sa.Column("include", JSONB, server_default="[]", nullable=False),
        sa.Column("exclude", JSONB, server_default="[]", nullable=False),
        sa.Column("max_depth", sa.Integer, server_default="2", nullable=False),
        sa.Column("max_pages", sa.Integer, server_default="500", nullable=False),
        sa.Column("render", sa.Text, server_default="never", nullable=False),
        sa.Column("refresh_hours", sa.Integer, server_default="168", nullable=False),
        sa.Column("status", sa.Text, server_default="active", nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("render IN ('never','auto','always')", name="ck_crawl_sources_render"),
        sa.CheckConstraint(
            "status IN ('active','paused','error','deleting')", name="ck_crawl_sources_status"
        ),
        sa.CheckConstraint("max_depth >= 0 AND max_pages > 0", name="ck_crawl_sources_limits"),
    )
    op.create_index("ix_crawl_sources_org", "crawl_sources", ["org_id"])
    op.create_table(
        "crawl_pages",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column(
            "source_id",
            sa.Text,
            sa.ForeignKey("crawl_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("final_url", sa.Text),
        sa.Column("depth", sa.Integer, server_default="0", nullable=False),
        sa.Column("content_hash", sa.Text),
        sa.Column("etag", sa.Text),
        sa.Column("last_modified", sa.Text),
        sa.Column("status", sa.Text, server_default="queued", nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("http_status", sa.Integer),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("vector_document_id", sa.Text),
        sa.UniqueConstraint("source_id", "url", name="uq_crawl_pages_source_url"),
        sa.CheckConstraint(
            "status IN ('queued','ok','not_modified','skipped_robots','error','gone')",
            name="ck_crawl_pages_status",
        ),
    )
    op.create_index("ix_crawl_pages_source_status", "crawl_pages", ["source_id", "status"])


def downgrade() -> None:
    op.drop_table("crawl_pages")
    op.drop_table("crawl_sources")
