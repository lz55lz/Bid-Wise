"""补齐文档节点清洗、父子关系与版面信息。

Revision ID: 20260831_0030
Revises: 20260831_0029
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0030"
down_revision: str | Sequence[str] | None = "20260831_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """原始节点不改写，新增字段仅保存可追溯的派生质量结果。"""
    op.add_column("document_versions", sa.Column("cleaning_summary", sa.JSON()))
    op.add_column("document_nodes", sa.Column("parent_node_id", sa.Uuid()))
    op.add_column(
        "document_nodes",
        sa.Column("tender_req_candidate", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("document_nodes", sa.Column("cleaned_content", sa.Text()))
    op.add_column(
        "document_nodes",
        sa.Column(
            "cleaning_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.add_column("document_nodes", sa.Column("bbox", sa.JSON()))
    op.create_foreign_key(
        "fk_document_nodes_parent_node",
        "document_nodes",
        "document_nodes",
        ["parent_node_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_document_nodes_parent_node_id", "document_nodes", ["parent_node_id"])
    op.alter_column("document_nodes", "tender_req_candidate", server_default=None)
    op.alter_column("document_nodes", "cleaning_metadata", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_document_nodes_parent_node_id", table_name="document_nodes")
    op.drop_constraint("fk_document_nodes_parent_node", "document_nodes", type_="foreignkey")
    op.drop_column("document_nodes", "bbox")
    op.drop_column("document_nodes", "cleaning_metadata")
    op.drop_column("document_nodes", "cleaned_content")
    op.drop_column("document_nodes", "tender_req_candidate")
    op.drop_column("document_nodes", "parent_node_id")
    op.drop_column("document_versions", "cleaning_summary")
