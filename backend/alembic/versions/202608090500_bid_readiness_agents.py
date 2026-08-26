"""Add durable LangGraph bid-readiness agent runs and node history."""

from alembic import op

revision = "202608090500"
down_revision = "202608090400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter type app.task_type add value if not exists 'RUN_BID_READINESS_AGENT'")
    op.execute(
        "alter table app.agent_runs add column source_document_version_id uuid references app.document_versions(id)"
    )
    op.execute("alter table app.agent_runs add column thread_id varchar(128)")
    op.execute(
        "alter table app.agent_runs add column checkpoint_version integer not null default 0"
    )
    op.execute(
        "alter table app.agent_runs add column requires_human_review boolean not null default false"
    )
    op.execute("alter table app.agent_runs drop constraint if exists agent_runs_status_check")
    op.execute("alter table app.agent_runs drop constraint if exists ck_agent_runs_status")
    op.execute(
        """
        alter table app.agent_runs add constraint ck_agent_runs_status check (
            status in ('QUEUED','RUNNING','WAITING_HUMAN','SUCCEEDED','FAILED','CANCELLED')
        )
        """
    )
    op.execute(
        """
        create table app.agent_run_steps (
          id uuid primary key default gen_random_uuid(),
          agent_run_id uuid not null references app.agent_runs(id),
          step_name varchar(64) not null,
          status varchar(16) not null check (status in ('QUEUED','RUNNING','SUCCEEDED','FAILED','SKIPPED')),
          attempt integer not null default 1,
          output_summary jsonb not null default '{}'::jsonb,
          error_code varchar(80),
          error_message text,
          started_at timestamptz,
          completed_at timestamptz,
          created_at timestamptz not null default now(),
          constraint uq_agent_run_step unique (agent_run_id, step_name)
        )
        """
    )
    op.execute(
        "create index ix_agent_run_steps_run on app.agent_run_steps (agent_run_id, created_at)"
    )
    op.execute(
        """
        create unique index uq_active_bid_readiness_agent_document
        on app.agent_runs (source_document_version_id)
        where workflow = 'BID_READINESS_REVIEW'
          and source_document_version_id is not null
          and status in ('QUEUED','RUNNING','WAITING_HUMAN')
        """
    )


def downgrade() -> None:
    op.execute("drop index if exists app.uq_active_bid_readiness_agent_document")
    op.execute("drop table if exists app.agent_run_steps")
    op.execute("alter table app.agent_runs drop constraint if exists ck_agent_runs_status")
    op.execute(
        """
        alter table app.agent_runs add constraint agent_runs_status_check check (
            status in ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')
        )
        """
    )
    op.execute("alter table app.agent_runs drop column if exists requires_human_review")
    op.execute("alter table app.agent_runs drop column if exists checkpoint_version")
    op.execute("alter table app.agent_runs drop column if exists thread_id")
    op.execute("alter table app.agent_runs drop column if exists source_document_version_id")
    # PostgreSQL enum labels intentionally remain after downgrade.
