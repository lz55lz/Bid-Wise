"""重解析时级联清理旧 Evidence 与向量。

Revision ID: 20260830_0006
Revises: 20260830_0005
Create Date: 2026-08-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260830_0006"
down_revision: str | Sequence[str] | None = "20260830_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """节点删除时自动删除其 Evidence；Evidence 删除时自动删除可重建向量。"""
    op.drop_constraint("evidences_document_node_id_fkey", "evidences", type_="foreignkey")
    op.create_foreign_key(
        "evidences_document_node_id_fkey",
        "evidences",
        "document_nodes",
        ["document_node_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "evidence_embeddings_evidence_id_fkey",
        "evidence_embeddings",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "evidence_embeddings_evidence_id_fkey",
        "evidence_embeddings",
        "evidences",
        ["evidence_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """回退到无级联外键，仅用于开发期迁移回滚。"""
    op.drop_constraint(
        "evidence_embeddings_evidence_id_fkey",
        "evidence_embeddings",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "evidence_embeddings_evidence_id_fkey",
        "evidence_embeddings",
        "evidences",
        ["evidence_id"],
        ["id"],
    )
    op.drop_constraint("evidences_document_node_id_fkey", "evidences", type_="foreignkey")
    op.create_foreign_key(
        "evidences_document_node_id_fkey",
        "evidences",
        "document_nodes",
        ["document_node_id"],
        ["id"],
    )
