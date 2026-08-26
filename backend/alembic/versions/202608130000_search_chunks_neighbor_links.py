"""Add pre_chunk_id and next_chunk_id to search_chunks for neighbor expansion.

Revision ID: 202608130000
Revises: add_cascade_deletes
Create Date: 2026-08-13

"""
from alembic import op
import sqlalchemy as sa


revision = "202608130000"
down_revision = "add_cascade_deletes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "search_chunks",
        sa.Column("pre_chunk_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "search_chunks",
        sa.Column("next_chunk_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_search_chunks_pre_chunk_id",
        "search_chunks",
        ["pre_chunk_id"],
        unique=False,
        postgresql_concurrently=True,
    )
    op.create_index(
        "ix_search_chunks_next_chunk_id",
        "search_chunks",
        ["next_chunk_id"],
        unique=False,
        postgresql_concurrently=True,
    )


def downgrade() -> None:
    op.drop_index("ix_search_chunks_next_chunk_id", "search_chunks", postgresql_concurrently=True)
    op.drop_index("ix_search_chunks_pre_chunk_id", "search_chunks", postgresql_concurrently=True)
    op.drop_column("search_chunks", "next_chunk_id")
    op.drop_column("search_chunks", "pre_chunk_id")
