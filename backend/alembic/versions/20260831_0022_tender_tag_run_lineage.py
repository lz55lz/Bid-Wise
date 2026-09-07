"""为文档标签补充 bid_pipeline 运行归属。

Revision ID: 20260831_0022
Revises: 20260831_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0022"
down_revision: str | Sequence[str] | None = "20260831_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """旧记录无法可靠回填运行 ID，保留 NULL；新管线写入必须关联运行。"""
    op.add_column("tender_document_tags", sa.Column("pipeline_run_id", sa.Uuid()))
    op.create_foreign_key(
        "tender_document_tags_pipeline_run_id_fkey",
        "tender_document_tags",
        "tender_pipeline_runs",
        ["pipeline_run_id"],
        ["id"],
    )
    op.create_index(
        "ix_tender_document_tags_pipeline_run_id",
        "tender_document_tags",
        ["pipeline_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tender_document_tags_pipeline_run_id", table_name="tender_document_tags")
    op.drop_constraint(
        "tender_document_tags_pipeline_run_id_fkey",
        "tender_document_tags",
        type_="foreignkey",
    )
    op.drop_column("tender_document_tags", "pipeline_run_id")
