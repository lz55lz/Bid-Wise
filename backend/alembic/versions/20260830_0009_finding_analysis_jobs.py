"""新增冻结 Evidence 输入的发现项分析任务。

Revision ID: 20260830_0009
Revises: 20260830_0008
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260830_0009"
down_revision: str | Sequence[str] | None = "20260830_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """记录可复现输入和异步状态，不把项目文本直接放进 Redis 任务载荷。"""
    op.create_table(
        "finding_analysis_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')"),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_finding_analysis_jobs_project_id", "finding_analysis_jobs", ["project_id"])
    op.create_index("ix_finding_analysis_jobs_input_hash", "finding_analysis_jobs", ["input_hash"])
    op.execute(
        "CREATE UNIQUE INDEX ux_finding_analysis_jobs_active_project "
        "ON finding_analysis_jobs (project_id) WHERE status IN ('QUEUED', 'RUNNING')"
    )


def downgrade() -> None:
    """删除任务事实表及其索引。"""
    op.execute("DROP INDEX IF EXISTS ux_finding_analysis_jobs_active_project")
    op.drop_index("ix_finding_analysis_jobs_input_hash", table_name="finding_analysis_jobs")
    op.drop_index("ix_finding_analysis_jobs_project_id", table_name="finding_analysis_jobs")
    op.drop_table("finding_analysis_jobs")
