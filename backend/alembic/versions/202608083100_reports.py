"""Create report versions and their evidence links."""

from alembic import op

revision = "202608083100"
down_revision = "202608083000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create type app.report_status as enum ('QUEUED', 'GENERATING', 'READY', 'FAILED');
        create table app.reports (
          id uuid primary key default gen_random_uuid(),
          project_id uuid not null references app.tender_projects(id) on delete restrict,
          version_no integer not null,
          status app.report_status not null default 'QUEUED',
          docx_object_key varchar(1024),
          pdf_object_key varchar(1024),
          error_code varchar(80),
          error_message text,
          generated_by uuid not null references app.users(id) on delete restrict,
          generated_at timestamptz,
          created_at timestamptz not null default now(),
          constraint uq_reports_version unique (project_id, version_no),
          constraint ck_reports_ready check (
            (status = 'READY' and docx_object_key is not null and pdf_object_key is not null)
            or status <> 'READY'
          )
        );
        create table app.report_sections (
          id uuid primary key default gen_random_uuid(),
          report_id uuid not null references app.reports(id) on delete restrict,
          section_code varchar(64) not null,
          order_no integer not null,
          content_markdown text not null,
          created_at timestamptz not null default now(),
          constraint uq_report_sections_order unique (report_id, order_no),
          constraint uq_report_sections_code unique (report_id, section_code)
        );
        create table app.report_evidences (
          report_section_id uuid not null references app.report_sections(id) on delete restrict,
          evidence_id uuid not null references app.evidences(id) on delete restrict,
          primary key (report_section_id, evidence_id)
        );
        create index ix_reports_project_version on app.reports (project_id, version_no desc);
    """)


def downgrade() -> None:
    op.execute("""
        drop table app.report_evidences;
        drop table app.report_sections;
        drop table app.reports;
        drop type app.report_status;
    """)
