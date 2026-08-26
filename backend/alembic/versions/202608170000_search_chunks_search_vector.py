"""Add search_vector tsvector column for BM25 full-text search.

This enables hybrid retrieval (vector + BM25 RRF fusion) matching WeKnora's
hybrid index pattern. The column stores to_tsvector('chinese_zh', content) for
Chinese full-text search with GIN index acceleration (zhparser parser).
"""

import sqlalchemy as sa

from alembic import op

revision = "202608170000"
down_revision = "202608160000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add tsvector column for BM25 search (stored as text for SQLAlchemy compatibility)
    op.add_column(
        "search_chunks",
        sa.Column("search_vector", sa.Text(), nullable=True),
    )
    # Create GIN index for fast full-text search
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_search_chunks_search_vector_gin
        ON app.search_chunks
        USING gin (to_tsvector('zh', content));
    """)
    # Backfill search_vector for existing rows (one-time)
    op.execute("""
        UPDATE app.search_chunks
        SET search_vector = to_tsvector('zh', content)
        WHERE search_vector IS NULL AND deleted_at IS NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_search_chunks_search_vector_gin")
    op.drop_column("search_chunks", "search_vector")
