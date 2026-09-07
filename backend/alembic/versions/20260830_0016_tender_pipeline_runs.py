# ruff: noqa: E501
"""保存 bid_pipeline 运行与节点阶段事实。

Revision ID: 20260830_0016
Revises: 20260830_0015
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260830_0016"
down_revision: str | Sequence[str] | None = "20260830_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建运行事实和阶段追踪表，不把运行状态放进 Redis。"""
    op.create_table(
        "tender_pipeline_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="QUEUED"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("arq_job_id", sa.String(length=128)),
        sa.Column("pending_review", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'WAITING_HUMAN_REVIEW', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="tender_pipeline_runs_status_check",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id"),
        sa.UniqueConstraint("arq_job_id"),
    )
    op.create_index("ix_tender_pipeline_runs_project_id", "tender_pipeline_runs", ["project_id"])
    op.create_index(
        "ix_tender_pipeline_runs_document_version_id",
        "tender_pipeline_runs",
        ["document_version_id"],
    )
    op.create_table(
        "tender_pipeline_stages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_run_id", sa.Uuid(), nullable=False),
        sa.Column("stage_name", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("input_summary", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("output_summary", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'WAITING_HUMAN_REVIEW', 'SKIPPED')",
            name="tender_pipeline_stages_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["tender_pipeline_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id", "stage_name"),
    )
    op.create_index(
        "ix_tender_pipeline_stages_pipeline_run_id",
        "tender_pipeline_stages",
        ["pipeline_run_id"],
    )


def downgrade() -> None:
    """开发期回退结构；生产运行记录应通过备份恢复。"""
    op.drop_index("ix_tender_pipeline_stages_pipeline_run_id", table_name="tender_pipeline_stages")
    op.drop_table("tender_pipeline_stages")
    op.drop_index("ix_tender_pipeline_runs_document_version_id", table_name="tender_pipeline_runs")
    op.drop_index("ix_tender_pipeline_runs_project_id", table_name="tender_pipeline_runs")
    op.drop_table("tender_pipeline_runs")
