"""新增完整分析运行，并扩展项目报告版本和章节。"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260831_0032"
down_revision: str | Sequence[str] | None = "20260831_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_analysis_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_stage", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stage_outputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('QUEUED', 'RUNNING', 'REPORT_QUEUED', 'SUCCEEDED', 'FAILED')"),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_analysis_runs_input_hash", "project_analysis_runs", ["input_hash"])
    op.create_index("ix_project_analysis_runs_report_id", "project_analysis_runs", ["report_id"])
    op.execute(
        "CREATE UNIQUE INDEX ux_project_analysis_runs_active_project ON project_analysis_runs "
        "(project_id) WHERE status IN ('QUEUED', 'RUNNING', 'REPORT_QUEUED')"
    )
    op.add_column("project_reports", sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("project_reports", sa.Column("analysis_run_id", sa.Uuid(), nullable=True))
    op.add_column(
        "project_reports",
        sa.Column("sections", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
    )
    op.execute("ALTER TABLE project_reports DROP CONSTRAINT IF EXISTS project_reports_report_type_check")
    op.create_check_constraint(
        "project_reports_report_type_check",
        "project_reports",
        "report_type IN ('SIMPLE', 'FULL', 'SUMMARY')",
    )
    op.create_index("ix_project_reports_analysis_run_id", "project_reports", ["analysis_run_id"])


def downgrade() -> None:
    op.drop_index("ix_project_reports_analysis_run_id", table_name="project_reports")
    op.drop_constraint("project_reports_report_type_check", "project_reports", type_="check")
    op.create_check_constraint("project_reports_report_type_check", "project_reports", "report_type IN ('SUMMARY')")
    op.drop_column("project_reports", "sections")
    op.drop_column("project_reports", "analysis_run_id")
    op.drop_column("project_reports", "version_no")
    op.execute("DROP INDEX IF EXISTS ux_project_analysis_runs_active_project")
    op.drop_index("ix_project_analysis_runs_report_id", table_name="project_analysis_runs")
    op.drop_index("ix_project_analysis_runs_input_hash", table_name="project_analysis_runs")
    op.drop_table("project_analysis_runs")
