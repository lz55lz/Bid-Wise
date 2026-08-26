"""Add reproducible project analysis runs and snapshots.

The project has no production-data compatibility requirement for this change;
the legacy bid_* tables remain available as document-pipeline intermediates.
"""

from alembic import op


revision = "202609040000"
down_revision = "202609030000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE app.task_type ADD VALUE IF NOT EXISTS 'RUN_PROJECT_ANALYSIS'")
    op.execute(
        """
        create table app.analysis_runs (
          id uuid primary key default gen_random_uuid(),
          project_id uuid not null references app.tender_projects(id) on delete restrict,
          status varchar(32) not null,
          current_stage varchar(64) not null,
          input_hash varchar(64) not null,
          task_id uuid references app.tasks(id) on delete set null,
          report_id uuid references app.reports(id) on delete set null,
          error_code varchar(80),
          error_message text,
          started_at timestamptz,
          completed_at timestamptz,
          created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id) on delete restrict
        );
        create index ix_analysis_runs_project_created on app.analysis_runs(project_id, created_at desc);
        create table app.analysis_snapshots (
          id uuid primary key default gen_random_uuid(),
          analysis_run_id uuid not null unique references app.analysis_runs(id) on delete restrict,
          tender_version_ids jsonb not null,
          enterprise_material_ids jsonb not null,
          rule_version_ids jsonb not null,
          input_hash varchar(64) not null,
          stage_outputs jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now()
        );
        """
    )


def downgrade() -> None:
    op.execute("drop table app.analysis_snapshots")
    op.execute("drop table app.analysis_runs")
