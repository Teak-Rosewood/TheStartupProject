"""vector search tables

Revision ID: vector_0001
Revises:

The embedding size is fixed per database: set ``VECTOR_DIMS`` (default 1024) before the first
migration. Changing it later means a new migration and re-embedding every chunk.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "vector_0001"
down_revision = None
branch_labels = ("vector",)
depends_on = None

DIMS = int(os.environ.get("VECTOR_DIMS", "1024"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "vector_collections",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("embed_model", sa.Text, nullable=False),
        sa.Column("dims", sa.Integer, nullable=False),
        sa.Column("backend", sa.Text, server_default="pgvector", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_vector_collections_org_name"),
    )
    op.create_table(
        "vector_documents",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column(
            "collection_id",
            sa.Text,
            sa.ForeignKey("vector_collections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Text),
        sa.Column("source_uri", sa.Text),
        sa.Column("title", sa.Text, server_default="", nullable=False),
        sa.Column("checksum", sa.Text, nullable=False),
        sa.Column("body", sa.Text),  # inline text until it is indexed, then cleared
        sa.Column("status", sa.Text, server_default="pending", nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("metadata", JSONB, server_default="{}", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("collection_id", "checksum", name="uq_vector_documents_checksum"),
        sa.CheckConstraint(
            "status IN ('pending','ready','failed','deleting')", name="ck_vector_documents_status"
        ),
    )
    op.create_index("ix_vector_documents_org", "vector_documents", ["org_id", "collection_id"])
    op.execute(
        f"""
        CREATE TABLE vector_chunks (
            id text PRIMARY KEY,
            org_id text NOT NULL,
            collection_id text NOT NULL REFERENCES vector_collections(id) ON DELETE CASCADE,
            document_id text NOT NULL REFERENCES vector_documents(id) ON DELETE CASCADE,
            ordinal integer NOT NULL,
            content text NOT NULL,
            token_count integer NOT NULL DEFAULT 0,
            embedding halfvec({DIMS}) NOT NULL,
            embed_model text NOT NULL,
            metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_vector_chunks_embedding ON vector_chunks "
        "USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute("CREATE INDEX ix_vector_chunks_tsv ON vector_chunks USING gin (tsv)")
    op.execute("CREATE INDEX ix_vector_chunks_doc ON vector_chunks (org_id, document_id)")
    # Row-level security on the table whose rows reach prompts. It is defence in depth: it only binds a
    # database role that is neither a superuser nor BYPASSRLS, so run the app as such a role. Every
    # query also filters by org_id itself. Collections and documents are left unprotected so that
    # cross-organization maintenance (a user's account erasure) can find them; they hold no vectors.
    op.execute("ALTER TABLE vector_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vector_chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY org_isolation ON vector_chunks "
        "USING (org_id = current_setting('app.org_id', true)) "
        "WITH CHECK (org_id = current_setting('app.org_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vector_chunks")
    op.drop_table("vector_documents")
    op.drop_table("vector_collections")
