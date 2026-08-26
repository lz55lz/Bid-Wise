"""Link reports to analysis runs and prevent concurrent project analyses."""

from alembic import op

revision = "202609050000"
down_revision = "202609040000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table app.reports add column analysis_run_id uuid references app.analysis_runs(id)")
    op.execute("create index ix_reports_analysis_run on app.reports(analysis_run_id)")
    op.execute(
        """
        create unique index ux_analysis_runs_one_active_per_project
        on app.analysis_runs(project_id)
        where status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
        """
    )


def downgrade() -> None:
    op.execute("drop index app.ux_analysis_runs_one_active_per_project")
    op.execute("drop index app.ix_reports_analysis_run")
    op.execute("alter table app.reports drop column analysis_run_id")
