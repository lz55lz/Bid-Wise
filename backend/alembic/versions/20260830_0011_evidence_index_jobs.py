"""新增 Evidence 向量索引异步任务。

Revision ID: 20260830_0011
Revises: 20260830_0010
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260830_0011"
down_revision: str | Sequence[str] | None = "20260830_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """将索引输入和执行状态落库，HTTP 请求不再等待嵌入模型。"""
    op.create_table(
        "evidence_index_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requested_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexed_count", sa.Integer(), nullable=False, server_default="0"),
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
    op.create_index("ix_evidence_index_jobs_project_id", "evidence_index_jobs", ["project_id"])
    op.execute(
        "CREATE UNIQUE INDEX ux_evidence_index_jobs_active_project "
        "ON evidence_index_jobs (project_id) WHERE status IN ('QUEUED', 'RUNNING')"
    )


def downgrade() -> None:
    """删除异步索引任务事实表及其互斥索引。"""
    op.execute("DROP INDEX IF EXISTS ux_evidence_index_jobs_active_project")
    op.drop_index("ix_evidence_index_jobs_project_id", table_name="evidence_index_jobs")
    op.drop_table("evidence_index_jobs")
