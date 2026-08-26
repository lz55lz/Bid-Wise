"""Create immutable document versions, parse nodes and asynchronous tasks."""

from alembic import op

revision = "202608081700"
down_revision = "202608081500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create type app.document_type as enum ('TENDER', 'ENTERPRISE');
        create type app.parse_status as enum (
          'UPLOADED', 'QUEUED', 'PARSING', 'PARSED', 'STRUCTURING', 'INDEXING', 'READY', 'FAILED'
        );
        create type app.document_node_type as enum (
          'SECTION', 'PARAGRAPH', 'TABLE', 'CELL', 'IMAGE', 'LIST'
        );
        create type app.task_type as enum (
          'PARSE_DOCUMENT', 'EXTRACT_REQUIREMENTS', 'INDEX_DOCUMENT', 'RUN_RISK_CHECK',
          'RUN_MATCH', 'GENERATE_DECISION', 'GENERATE_REPORT', 'RAG_ANSWER'
        );
        create type app.task_status as enum ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED');

        create table app.documents (
          id uuid primary key default gen_random_uuid(),
          project_id uuid references app.tender_projects(id) on delete restrict,
          document_type app.document_type not null,
          logical_name varchar(512) not null,
          current_version_id uuid,
          created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id) on delete restrict,
          deleted_at timestamptz,
          constraint ck_documents_scope check (
            (document_type = 'TENDER' and project_id is not null) or
            (document_type = 'ENTERPRISE' and project_id is null)
          ),
          constraint uq_documents_logical_name unique (project_id, document_type, logical_name)
        );

        create table app.document_versions (
          id uuid primary key default gen_random_uuid(),
          document_id uuid not null references app.documents(id) on delete restrict,
          version_no integer not null,
          file_name varchar(512) not null,
          file_size bigint not null,
          mime_type varchar(128) not null,
          object_key varchar(1024) not null,
          sha256 char(64) not null,
          parse_status app.parse_status not null default 'UPLOADED',
          parse_output_key varchar(1024),
          error_code varchar(80),
          error_message text,
          created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id) on delete restrict,
          completed_at timestamptz,
          constraint uq_document_versions_no unique (document_id, version_no),
          constraint uq_document_versions_object_key unique (object_key),
          constraint ck_document_versions_size check (file_size > 0),
          constraint ck_document_versions_status_error check (
            (parse_status = 'FAILED' and error_code is not null) or parse_status <> 'FAILED'
          )
        );
        alter table app.documents add constraint fk_documents_current_version
          foreign key (current_version_id) references app.document_versions(id) on delete restrict;

        create table app.document_nodes (
          id uuid primary key default gen_random_uuid(),
          document_version_id uuid not null references app.document_versions(id) on delete restrict,
          parent_node_id uuid references app.document_nodes(id) on delete restrict,
          node_type app.document_node_type not null,
          page_number integer,
          section_path varchar(1024),
          order_no integer not null,
          content text not null,
          content_hash char(64) not null,
          bbox jsonb,
          metadata jsonb not null default '{}'::jsonb,
          created_at timestamptz not null default now(),
          constraint uq_document_nodes_order unique (document_version_id, order_no),
          constraint ck_document_nodes_page check (page_number is null or page_number > 0),
          constraint ck_document_nodes_bbox check (bbox is null or jsonb_typeof(bbox) = 'object')
        );

        create table app.tasks (
          id uuid primary key default gen_random_uuid(),
          task_type app.task_type not null,
          target_type varchar(64) not null,
          target_id uuid not null,
          idempotency_key varchar(128) not null,
          status app.task_status not null default 'QUEUED',
          attempt integer not null default 1,
          parent_task_id uuid references app.tasks(id) on delete restrict,
          celery_task_id varchar(128),
          error_code varchar(80),
          error_message text,
          started_at timestamptz,
          completed_at timestamptz,
          created_at timestamptz not null default now(),
          created_by uuid references app.users(id) on delete restrict,
          constraint uq_tasks_idempotency unique (task_type, idempotency_key, attempt),
          constraint ck_tasks_attempt check (attempt > 0)
        );

        create table app.task_events (
          id bigint generated always as identity primary key,
          task_id uuid not null references app.tasks(id) on delete restrict,
          from_status app.task_status,
          to_status app.task_status not null,
          message text,
          created_at timestamptz not null default now()
        );

        create or replace function app.validate_document_current_version()
        returns trigger language plpgsql as $$
        begin
          if new.current_version_id is not null and not exists (
            select 1 from app.document_versions version
            where version.id = new.current_version_id and version.document_id = new.id
          ) then
            raise exception 'current_version_id must belong to the same document';
          end if;
          return new;
        end;
        $$;
        create constraint trigger trg_documents_current_version
          after insert or update of current_version_id on app.documents
          deferrable initially deferred
          for each row execute function app.validate_document_current_version();

        create index ix_documents_project on app.documents (project_id, created_at desc)
          where deleted_at is null;
        create index ix_document_versions_status on app.document_versions (parse_status, created_at);
        create index ix_document_nodes_version_page on app.document_nodes (document_version_id, page_number, order_no);
        create index ix_tasks_target_created on app.tasks (target_type, target_id, created_at desc);
        create index ix_task_events_task_created on app.task_events (task_id, created_at);
    """)


def downgrade() -> None:
    op.execute("""
        drop trigger trg_documents_current_version on app.documents;
        drop function app.validate_document_current_version();
        drop table app.task_events;
        drop table app.tasks;
        drop table app.document_nodes;
        alter table app.documents drop constraint fk_documents_current_version;
        drop table app.document_versions;
        drop table app.documents;
        drop type app.task_status;
        drop type app.task_type;
        drop type app.document_node_type;
        drop type app.parse_status;
        drop type app.document_type;
    """)
