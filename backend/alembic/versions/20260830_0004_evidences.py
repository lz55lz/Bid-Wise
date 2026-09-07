"""创建项目授权锚点 Evidence 表。

Revision ID: 20260830_0004
Revises: 20260830_0003
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0004"
down_revision: str | Sequence[str] | None = "20260830_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为所有可引用结论建立统一项目归属与来源定位字段。"""
    op.create_table(
        "evidences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("document_version_id", sa.Uuid()),
        sa.Column("document_node_id", sa.Uuid()),
        sa.Column("quoted_text", sa.Text()),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.CheckConstraint("source_type IN ('DOCUMENT_NODE', 'USER_CONFIRMATION', 'SYSTEM_RULE')"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["document_node_id"], ["document_nodes.id"]),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidences_project_id", "evidences", ["project_id"])
    op.create_index("ix_evidences_document_node_id", "evidences", ["document_node_id"])
