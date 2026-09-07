"""新增基于确认发现项的项目报告。

Revision ID: 20260830_0010
Revises: 20260830_0009
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260830_0010"
down_revision: str | Sequence[str] | None = "20260830_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """报告冻结确认结论和证据输入，Markdown 正文与引用一并持久化。"""
    op.create_table(
        "project_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("report_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=True),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("report_type IN ('SUMMARY')"),
        sa.CheckConstraint("status IN ('QUEUED', 'GENERATING', 'READY', 'FAILED')"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_reports_project_id", "project_reports", ["project_id"])
    op.create_index("ix_project_reports_input_hash", "project_reports", ["input_hash"])
    op.execute(
        "CREATE UNIQUE INDEX ux_project_reports_active_project "
        "ON project_reports (project_id) WHERE status IN ('QUEUED', 'GENERATING')"
    )


def downgrade() -> None:
    """删除报告及其运行态唯一索引。"""
    op.execute("DROP INDEX IF EXISTS ux_project_reports_active_project")
    op.drop_index("ix_project_reports_input_hash", table_name="project_reports")
    op.drop_index("ix_project_reports_project_id", table_name="project_reports")
    op.drop_table("project_reports")
