"""Add embedding column to search_chunks for pgvector.

This enables storing embedding vectors directly in PostgreSQL
using pgvector (0.8.5+) with HNSW index for fast ANN search.
"""

from alembic import op
import sqlalchemy as sa


revision = "202608100000"
down_revision = "202608091100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add embedding column using vector type (requires pgvector extension)
    # The column is stored as text representation for portability
    op.add_column(
        "search_chunks",
        sa.Column("embedding", sa.Text(), nullable=True),
    )
    # Create HNSW index for fast approximate nearest neighbor search.
    # This requires pgvector 0.8.5+ and the column to be of vector type.
    # After adding the column, run via docker exec in a separate transaction:
    #   docker exec pg-server psql -U admin -d ai_bid_advisor -c \
    #     "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_search_chunks_embedding_hnsw
    #      ON app.search_chunks USING hnsw (embedding vector_cosine_ops)
    #      WITH (m = 16, ef_construction = 64) WHERE embedding IS NOT NULL;"
    #
    # For now (migration-safe), create a basic index without CONCURRENTLY:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_search_chunks_embedding_hnsw
        ON app.search_chunks
        USING hnsw ((embedding::vector(1024)) vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_search_chunks_embedding_hnsw")
    op.drop_column("search_chunks", "embedding")

