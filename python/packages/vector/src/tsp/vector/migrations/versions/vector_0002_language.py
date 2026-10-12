"""per-chunk language and Hindi-aware full-text search

Revision ID: vector_0002
Revises: vector_0001
"""

from alembic import op

revision = "vector_0002"
down_revision = "vector_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The characters are written as unicode escapes so the migration survives editors and diffs.
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION tsp_norm_devanagari(t text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
          SELECT replace(replace(translate(
            regexp_replace(t, E'[‌‍]', '', 'g'),
            E'क़ख़ग़ज़ड़ढ़फ़य़',
            E'कखगजडढफय'
          ), E'़', ''), E'ँ', E'ं')
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tsp_tsv(lang text, content text) RETURNS tsvector
        LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
          SELECT CASE lang
            WHEN 'hi' THEN to_tsvector('simple', tsp_norm_devanagari(content))
            WHEN 'en' THEN to_tsvector('english', content)
            ELSE to_tsvector('simple', content)
          END
        $$
        """
    )
    op.execute("ALTER TABLE vector_chunks ADD COLUMN lang text NOT NULL DEFAULT 'en'")
    op.execute("DROP INDEX ix_vector_chunks_tsv")
    op.execute("ALTER TABLE vector_chunks DROP COLUMN tsv")
    op.execute(
        "ALTER TABLE vector_chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (tsp_tsv(lang, content)) STORED"
    )
    op.execute("CREATE INDEX ix_vector_chunks_tsv ON vector_chunks USING gin (tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_vector_chunks_tsv")
    op.execute("ALTER TABLE vector_chunks DROP COLUMN tsv")
    op.execute(
        "ALTER TABLE vector_chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED"
    )
    op.execute("CREATE INDEX ix_vector_chunks_tsv ON vector_chunks USING gin (tsv)")
    op.execute("ALTER TABLE vector_chunks DROP COLUMN lang")
    op.execute("DROP FUNCTION tsp_tsv(text, text)")
    op.execute("DROP FUNCTION tsp_norm_devanagari(text)")
