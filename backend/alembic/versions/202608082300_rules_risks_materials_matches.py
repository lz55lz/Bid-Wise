"""Create P0 rules, risks, enterprise materials and match persistence."""

from alembic import op

revision = "202608082300"
down_revision = "202608082200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create type app.risk_type as enum (
          'QUALIFICATION', 'COMPLIANCE', 'FORMAT', 'TIME',
          'FINANCIAL', 'TECHNICAL', 'BUSINESS', 'DOCUMENT'
        );
        create type app.risk_severity as enum ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO');
        create type app.risk_status as enum (
          'PENDING', 'CONFIRMED', 'RESOLVED', 'FALSE_POSITIVE', 'IGNORED'
        );
        create type app.material_type as enum (
          'QUALIFICATION', 'CERTIFICATE', 'PROJECT_EXPERIENCE', 'PERSONNEL'
        );
        create type app.match_status as enum (
          'MATCHED', 'PARTIAL', 'MISSING', 'EXPIRED', 'UNKNOWN', 'CONFLICT'
        );

        create table app.rules (
          id uuid primary key default gen_random_uuid(),
          code varchar(80) not null,
          name varchar(256) not null,
          risk_type app.risk_type not null,
          created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id) on delete restrict,
          constraint uq_rules_code unique (code)
        );
        create table app.rule_versions (
          id uuid primary key default gen_random_uuid(),
          rule_id uuid not null references app.rules(id) on delete restrict,
          version_no integer not null,
          severity app.risk_severity not null,
          definition jsonb not null,
          is_enabled boolean not null default false,
          effective_at timestamptz not null default now(),
          retired_at timestamptz,
          created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id) on delete restrict,
          constraint uq_rule_versions_no unique (rule_id, version_no),
          constraint ck_rule_versions_dates check (retired_at is null or retired_at > effective_at)
        );
        create table app.risks (
          id uuid primary key default gen_random_uuid(),
          project_id uuid not null references app.tender_projects(id) on delete restrict,
          rule_version_id uuid references app.rule_versions(id) on delete restrict,
          risk_type app.risk_type not null,
          severity app.risk_severity not null,
          title varchar(512) not null,
          description text not null,
          trigger_data jsonb not null default '{}'::jsonb,
          confidence numeric(5,4),
          status app.risk_status not null default 'PENDING',
          resolution text,
          primary_evidence_id uuid references app.evidences(id) on delete restrict,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          constraint ck_risks_confidence check (confidence is null or confidence between 0 and 1),
          constraint ck_risks_resolution check (status = 'PENDING' or resolution is not null)
        );
        create table app.risk_evidences (
          risk_id uuid not null references app.risks(id) on delete restrict,
          evidence_id uuid not null references app.evidences(id) on delete restrict,
          created_at timestamptz not null default now(),
          primary key (risk_id, evidence_id)
        );
        create table app.risk_reviews (
          id uuid primary key default gen_random_uuid(),
          risk_id uuid not null references app.risks(id) on delete restrict,
          from_status app.risk_status,
          to_status app.risk_status not null,
          resolution text not null,
          reviewed_by uuid not null references app.users(id) on delete restrict,
          reviewed_at timestamptz not null default now()
        );

        create table app.enterprise_materials (
          id uuid primary key default gen_random_uuid(),
          material_type app.material_type not null,
          name varchar(512) not null,
          material_no varchar(128),
          issuer varchar(256),
          level varchar(128),
          valid_from date,
          valid_to date,
          amount numeric(18,2),
          currency char(3) not null default 'CNY',
          attributes jsonb not null default '{}'::jsonb,
          status app.review_status not null default 'CONFIRMED',
          created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id) on delete restrict,
          updated_at timestamptz not null default now(),
          updated_by uuid not null references app.users(id) on delete restrict,
          deleted_at timestamptz,
          constraint ck_enterprise_material_dates check (
            valid_to is null or valid_from is null or valid_to >= valid_from
          ),
          constraint ck_enterprise_material_amount check (amount is null or amount >= 0)
        );
        create table app.material_documents (
          material_id uuid not null references app.enterprise_materials(id) on delete restrict,
          document_id uuid not null references app.documents(id) on delete restrict,
          document_version_id uuid not null references app.document_versions(id) on delete restrict,
          relation varchar(32) not null default 'PROOF',
          created_at timestamptz not null default now(),
          primary key (material_id, document_version_id)
        );
        create table app.match_results (
          id uuid primary key default gen_random_uuid(),
          project_id uuid not null references app.tender_projects(id) on delete restrict,
          requirement_id uuid not null references app.requirements(id) on delete restrict,
          material_id uuid references app.enterprise_materials(id) on delete restrict,
          automatic_status app.match_status not null,
          final_status app.match_status not null,
          reason text not null,
          missing_conditions jsonb not null default '[]'::jsonb,
          is_overridden boolean not null default false,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          constraint uq_match_results_pair unique (requirement_id, material_id)
        );
        create table app.match_evidences (
          match_result_id uuid not null references app.match_results(id) on delete restrict,
          evidence_id uuid not null references app.evidences(id) on delete restrict,
          side varchar(16) not null check (side in ('REQUIREMENT', 'MATERIAL', 'MISSING')),
          primary key (match_result_id, evidence_id, side)
        );
        create table app.match_overrides (
          id uuid primary key default gen_random_uuid(),
          match_result_id uuid not null references app.match_results(id) on delete restrict,
          previous_status app.match_status not null,
          final_status app.match_status not null,
          override_reason text not null,
          overridden_by uuid not null references app.users(id) on delete restrict,
          overridden_at timestamptz not null default now()
        );

        create or replace function app.validate_material_document_version()
        returns trigger language plpgsql as $$
        declare
          version_document_id uuid;
          version_document_type app.document_type;
        begin
          select version.document_id, document.document_type
            into version_document_id, version_document_type
            from app.document_versions version
            join app.documents document on document.id = version.document_id
           where version.id = new.document_version_id;
          if version_document_id is null
             or version_document_id <> new.document_id
             or version_document_type <> 'ENTERPRISE' then
            raise exception 'material proof must reference its ENTERPRISE document version';
          end if;
          return new;
        end;
        $$;
        create trigger trg_material_documents_validate_version
          before insert or update on app.material_documents
          for each row execute function app.validate_material_document_version();

        create index ix_rule_versions_enabled on app.rule_versions (is_enabled, effective_at)
          where retired_at is null;
        create index ix_risks_project_status_severity
          on app.risks (project_id, status, severity);
        create index ix_materials_type_valid_to on app.enterprise_materials (material_type, valid_to)
          where deleted_at is null;
        create index ix_matches_project_requirement
          on app.match_results (project_id, requirement_id, final_status);
        create unique index uq_match_results_missing_requirement
          on app.match_results (requirement_id) where material_id is null;
    """)


def downgrade() -> None:
    op.execute("""
        drop trigger trg_material_documents_validate_version on app.material_documents;
        drop function app.validate_material_document_version();
        drop table app.match_overrides;
        drop table app.match_evidences;
        drop table app.match_results;
        drop table app.material_documents;
        drop table app.enterprise_materials;
        drop table app.risk_reviews;
        drop table app.risk_evidences;
        drop table app.risks;
        drop table app.rule_versions;
        drop table app.rules;
        drop type app.match_status;
        drop type app.material_type;
        drop type app.risk_status;
        drop type app.risk_severity;
        drop type app.risk_type;
    """)
