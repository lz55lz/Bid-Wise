"""把 ProjectAnalysisRun 收敛为纯运行记录。

Revision ID: 20260903_0046
Revises: 20260903_0045
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0046"
down_revision: str | Sequence[str] | None = "20260903_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_project_analysis_runs_report_id", table_name="project_analysis_runs")
    op.drop_column("project_analysis_runs", "report_id")


def downgrade() -> None:
    op.add_column(
        "project_analysis_runs",
        sa.Column("report_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_project_analysis_runs_report_id",
        "project_analysis_runs",
        ["report_id"],
    )
