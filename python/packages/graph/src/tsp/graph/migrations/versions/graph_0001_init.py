"""knowledge graph

Revision ID: graph_0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "graph_0001"
down_revision = None
branch_labels = ("graph",)
depends_on = None


def _id() -> sa.Column:
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _scope() -> list[sa.Column]:
    return [
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "g_entities",
        _id(),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("attrs", JSONB, server_default="{}", nullable=False),
        sa.Column("external_ids", JSONB, server_default="{}", nullable=False),
        *_scope(),
    )
    op.create_index("ix_g_entities_org_kind", "g_entities", ["org_id", "kind"])
    op.create_table(
        "g_aliases",
        _id(),
        sa.Column(
            "entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("g_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text, nullable=False),
        sa.Column("lang", sa.Text),
        sa.Column("normalised", sa.Text, nullable=False),
        *_scope(),
        sa.UniqueConstraint("entity_id", "normalised", name="uq_g_aliases_entity_normalised"),
    )
    op.create_index("ix_g_aliases_entity_id", "g_aliases", ["entity_id"])
    op.create_index("ix_g_aliases_org_normalised", "g_aliases", ["org_id", "normalised"])
    op.create_index(
        "ix_g_aliases_normalised_trgm",
        "g_aliases",
        ["normalised"],
        postgresql_using="gin",
        postgresql_ops={"normalised": "gin_trgm_ops"},
    )
    op.create_table(
        "g_documents",
        _id(),
        sa.Column("source_ref", sa.Text),
        sa.Column("vector_document_id", UUID(as_uuid=True)),
        sa.Column("title", sa.Text, server_default="", nullable=False),
        sa.Column("published_at", sa.Date),
        sa.Column("kind", sa.Text),
        sa.Column("tier", sa.Text),
        sa.Column("lang", sa.Text),
        sa.Column("attrs", JSONB, server_default="{}", nullable=False),
        *_scope(),
    )
    op.create_index("ix_g_documents_org_source", "g_documents", ["org_id", "source_ref"])
    op.create_table(
        "g_claims",
        _id(),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "subject_id",
            UUID(as_uuid=True),
            sa.ForeignKey("g_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("predicate", sa.Text, nullable=False),
        sa.Column(
            "object_id", UUID(as_uuid=True), sa.ForeignKey("g_entities.id", ondelete="SET NULL")
        ),
        sa.Column("object_value", JSONB),
        sa.Column(
            "document_id", UUID(as_uuid=True), sa.ForeignKey("g_documents.id", ondelete="SET NULL")
        ),
        sa.Column("chunk_id", UUID(as_uuid=True)),
        sa.Column("quote_start", sa.Integer),
        sa.Column("quote_end", sa.Integer),
        sa.Column("confidence", sa.Float),
        sa.Column("extracted_by", sa.Text),
        sa.Column("status", sa.Text, server_default="auto", nullable=False),
        *_scope(),
        sa.CheckConstraint("status IN ('auto', 'verified', 'rejected')", name="ck_g_claims_status"),
    )
    op.create_index("ix_g_claims_org_subject", "g_claims", ["org_id", "subject_id"])
    op.create_index("ix_g_claims_document", "g_claims", ["document_id"])
    op.create_table(
        "g_links",
        _id(),
        sa.Column("src_kind", sa.Text, nullable=False),
        sa.Column("src_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rel", sa.Text, nullable=False),
        sa.Column("dst_kind", sa.Text, nullable=False),
        sa.Column("dst_id", UUID(as_uuid=True), nullable=False),
        sa.Column("valid_from", sa.Date),
        sa.Column("valid_to", sa.Date),
        sa.Column("attrs", JSONB, server_default="{}", nullable=False),
        sa.Column(
            "evidence_claim_id",
            UUID(as_uuid=True),
            sa.ForeignKey("g_claims.id", ondelete="SET NULL"),
        ),
        *_scope(),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_g_links_valid_range",
        ),
    )
    op.create_index("ix_g_links_src", "g_links", ["org_id", "src_kind", "src_id", "rel"])
    op.create_index("ix_g_links_dst", "g_links", ["org_id", "dst_kind", "dst_id", "rel"])
    op.create_table(
        "g_stances",
        _id(),
        sa.Column(
            "holder_id",
            UUID(as_uuid=True),
            sa.ForeignKey("g_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic_key", sa.Text, nullable=False),
        sa.Column("position", sa.Numeric),
        sa.Column("label", sa.Text),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column(
            "evidence_claim_id",
            UUID(as_uuid=True),
            sa.ForeignKey("g_claims.id", ondelete="SET NULL"),
        ),
        sa.Column("confidence", sa.Float),
        *_scope(),
        sa.CheckConstraint(
            "position IS NULL OR position BETWEEN -1 AND 1", name="ck_g_stances_position"
        ),
    )
    op.create_index("ix_g_stances_org_topic", "g_stances", ["org_id", "topic_key", "as_of"])
    op.create_table(
        "g_series",
        _id(),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("unit", sa.Text),
        sa.Column("frequency", sa.Text),
        sa.Column("source_ref", sa.Text),
        sa.Column("attrs", JSONB, server_default="{}", nullable=False),
        *_scope(),
        sa.UniqueConstraint("org_id", "key", name="uq_g_series_org_key"),
    )
    op.create_table(
        "g_observations",
        sa.Column(
            "series_id",
            UUID(as_uuid=True),
            sa.ForeignKey("g_series.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("period_start", sa.Date, primary_key=True),
        sa.Column("estimate_type", sa.Text, primary_key=True, server_default="actual"),
        sa.Column("vintage", sa.Date, primary_key=True, server_default=sa.func.current_date()),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("value", sa.Numeric, nullable=False),
        sa.Column("attrs", JSONB, server_default="{}", nullable=False),
        *_scope(),
    )
    op.create_table(
        "g_enrichments",
        _id(),
        sa.Column("target_kind", sa.Text, nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("enricher", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("status", sa.Text, server_default="running", nullable=False),
        sa.Column(
            "ran_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *_scope(),
        sa.UniqueConstraint(
            "org_id",
            "target_kind",
            "target_id",
            "enricher",
            "version",
            name="uq_g_enrichments_target",
        ),
    )


def downgrade() -> None:
    for table in (
        "g_enrichments",
        "g_observations",
        "g_series",
        "g_stances",
        "g_links",
        "g_claims",
        "g_documents",
        "g_aliases",
        "g_entities",
    ):
        op.drop_table(table)
