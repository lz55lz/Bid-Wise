"""Create versioned decision persistence."""

from alembic import op

revision = "202608083000"
down_revision = "202608082300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create type app.decision_suggestion as enum ('RECOMMEND', 'CAUTION', 'HOLD', 'REJECT');
        create type app.final_decision as enum ('BID', 'ABANDON');
        create table app.decisions (
          id uuid primary key default gen_random_uuid(),
          project_id uuid not null references app.tender_projects(id) on delete restrict,
          suggestion app.decision_suggestion not null,
          hard_constraint_result jsonb not null default '{}'::jsonb,
          reason text not null,
          missing_materials jsonb not null default '[]'::jsonb,
          final_decision app.final_decision,
          confirmed_by uuid references app.users(id) on delete restrict,
          confirmed_at timestamptz,
          created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id) on delete restrict,
          constraint ck_decisions_confirmation check (
            (final_decision is null and confirmed_by is null and confirmed_at is null) or
            (final_decision is not null and confirmed_by is not null and confirmed_at is not null)
          )
        );
        create table app.decision_evidences (
          decision_id uuid not null references app.decisions(id) on delete restrict,
          evidence_id uuid not null references app.evidences(id) on delete restrict,
          primary key (decision_id, evidence_id)
        );
        create index ix_decisions_project_created on app.decisions (project_id, created_at desc);
    """)


def downgrade() -> None:
    op.execute("""
        drop table app.decision_evidences;
        drop table app.decisions;
        drop type app.final_decision;
        drop type app.decision_suggestion;
    """)
