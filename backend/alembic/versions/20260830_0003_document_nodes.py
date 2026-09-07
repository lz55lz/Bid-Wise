"""创建 MinerU 解析节点表。

Revision ID: 20260830_0003
Revises: 20260830_0002
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0003"
down_revision: str | Sequence[str] | None = "20260830_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """节点只归属某一不可变版本，避免新版本覆盖旧证据定位。"""
    op.create_table(
        "document_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("node_type", sa.String(length=16), nullable=False),
        sa.Column("page_number", sa.Integer()),
        sa.Column("section_path", sa.String(length=1024)),
        sa.Column("order_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("node_type IN ('SECTION', 'PARAGRAPH', 'TABLE', 'LIST', 'IMAGE')"),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_version_id", "order_no"),
    )
    op.create_index(
        "ix_document_nodes_document_version_id",
        "document_nodes",
        ["document_version_id"],
    )


def downgrade() -> None:
    """移除解析节点；源文件版本仍由上一迁移保留。"""
    op.drop_index("ix_document_nodes_document_version_id", table_name="document_nodes")
    op.drop_table("document_nodes")
