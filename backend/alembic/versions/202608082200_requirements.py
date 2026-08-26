"""Create Evidence-gated project fields and requirements."""

from alembic import op

revision = "202608082200"
down_revision = "202608082100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create type app.review_status as enum ('PENDING', 'CONFIRMED', 'REJECTED');
        create type app.requirement_category as enum (
          'PROJECT', 'QUALIFICATION', 'BUSINESS', 'SCORING'
        );
        create table app.project_fields (
          id uuid primary key default gen_random_uuid(),
          project_id uuid not null references app.tender_projects(id) on delete restrict,
          field_code varchar(80) not null,
          value_json jsonb not null,
          confidence numeric(5,4),
          review_status app.review_status not null default 'PENDING',
          primary_evidence_id uuid references app.evidences(id) on delete restrict,
          reviewed_at timestamptz,
          reviewed_by uuid references app.users(id) on delete restrict,
          review_note text,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          constraint uq_project_fields_code unique (project_id, field_code),
          constraint ck_project_fields_confidence check (
            confidence is null or confidence between 0 and 1
          )
        );
        create table app.requirements (
          id uuid primary key default gen_random_uuid(),
          project_id uuid not null references app.tender_projects(id) on delete restrict,
          category app.requirement_category not null,
          title varchar(512) not null,
          description text,
          conditions jsonb not null default '{}'::jsonb,
          is_mandatory boolean not null default false,
          score numeric(10,2),
          confidence numeric(5,4),
          review_status app.review_status not null default 'PENDING',
          primary_evidence_id uuid references app.evidences(id) on delete restrict,
          reviewed_at timestamptz,
          reviewed_by uuid references app.users(id) on delete restrict,
          review_note text,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          deleted_at timestamptz,
          constraint ck_requirements_confidence check (
            confidence is null or confidence between 0 and 1
          ),
          constraint ck_requirements_score check (score is null or score >= 0)
        );
        create table app.requirement_evidences (
          requirement_id uuid not null references app.requirements(id) on delete restrict,
          evidence_id uuid not null references app.evidences(id) on delete restrict,
          relation varchar(32) not null default 'SOURCE',
          created_at timestamptz not null default now(),
          primary key (requirement_id, evidence_id)
        );
        create index ix_requirements_project_category on app.requirements (
          project_id, category, review_status
        ) where deleted_at is null;
    """)


def downgrade() -> None:
    op.execute("""
        drop table app.requirement_evidences;
        drop table app.requirements;
        drop table app.project_fields;
        drop type app.requirement_category;
        drop type app.review_status;
    """)
