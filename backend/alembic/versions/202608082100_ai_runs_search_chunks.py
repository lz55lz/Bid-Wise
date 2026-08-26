"""Create AI run audit records and rebuildable search chunk metadata."""

from alembic import op

revision = "202608082100"
down_revision = "202608082000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create type app.ai_run_status as enum (
          'RUNNING', 'SUCCEEDED', 'FAILED', 'VALIDATION_FAILED'
        );

        create table app.prompt_templates (
          id uuid primary key default gen_random_uuid(),
          scene varchar(80) not null,
          version varchar(32) not null,
          template text not null,
          schema_json jsonb not null,
          is_active boolean not null default false,
          created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id) on delete restrict,
          constraint uq_prompt_templates_scene_version unique (scene, version)
        );

        create table app.ai_runs (
          id uuid primary key default gen_random_uuid(),
          task_id uuid references app.tasks(id) on delete restrict,
          scene varchar(80) not null,
          prompt_template_id uuid references app.prompt_templates(id) on delete restrict,
          model_id varchar(32) not null,
          input_hash char(64) not null,
          output_hash char(64),
          status app.ai_run_status not null default 'RUNNING',
          latency_ms integer,
          error_code varchar(80),
          created_at timestamptz not null default now(),
          completed_at timestamptz,
          constraint ck_ai_runs_model_id check (model_id in ('deepseekv4', 'rankv2', 'bge-m3')),
          constraint ck_ai_runs_latency check (latency_ms is null or latency_ms >= 0)
        );

        create table app.ai_run_evidences (
          ai_run_id uuid not null references app.ai_runs(id) on delete restrict,
          evidence_id uuid not null references app.evidences(id) on delete restrict,
          primary key (ai_run_id, evidence_id)
        );

        create table app.search_chunks (
          id uuid primary key default gen_random_uuid(),
          source_document_version_id uuid references app.document_versions(id) on delete restrict,
          source_node_id uuid references app.document_nodes(id) on delete restrict,
          evidence_id uuid references app.evidences(id) on delete restrict,
          project_id uuid references app.tender_projects(id) on delete restrict,
          chunk_type varchar(32) not null check (chunk_type in ('TENDER', 'ENTERPRISE')),
          chunk_index integer not null,
          content text not null,
          content_hash char(64) not null,
          milvus_pk varchar(64) not null,
          metadata jsonb not null default '{}'::jsonb,
          indexed_at timestamptz,
          deleted_at timestamptz,
          constraint uq_search_chunks_source unique (source_node_id, chunk_index),
          constraint uq_search_chunks_milvus_pk unique (milvus_pk)
        );

        create index ix_search_chunks_project
          on app.search_chunks (project_id, chunk_type) where deleted_at is null;
    """)


def downgrade() -> None:
    op.execute("""
        drop table app.search_chunks;
        drop table app.ai_run_evidences;
        drop table app.ai_runs;
        drop table app.prompt_templates;
        drop type app.ai_run_status;
    """)
