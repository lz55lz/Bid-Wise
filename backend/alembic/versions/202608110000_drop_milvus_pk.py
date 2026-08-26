"""Drop milvus_pk column from search_chunks (pgvector-only, Milvus abandoned)."""

from alembic import op
import sqlalchemy as sa


revision = "202608110000"
down_revision = "202608100000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("search_chunks", "milvus_pk")


def downgrade() -> None:
    op.add_column(
        "search_chunks",
        sa.Column("milvus_pk", sa.String(64), nullable=False),
    )
