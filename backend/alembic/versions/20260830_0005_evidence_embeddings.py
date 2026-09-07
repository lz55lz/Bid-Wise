"""启用 pgvector 并创建 Evidence 向量索引。

Revision ID: 20260830_0005
Revises: 20260830_0004
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "20260830_0005"
down_revision: str | Sequence[str] | None = "20260830_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """向量是可重建索引；业务事实仍由 Evidence 与项目授权关系保存。"""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "evidence_embeddings",
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidences.id"]),
        sa.PrimaryKeyConstraint("evidence_id"),
    )
    op.execute(
        "CREATE INDEX ix_evidence_embeddings_hnsw "
        "ON evidence_embeddings USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    """删除可重建向量索引；保留 Evidence 原始事实记录。"""
    op.execute("DROP INDEX IF EXISTS ix_evidence_embeddings_hnsw")
    op.drop_table("evidence_embeddings")
