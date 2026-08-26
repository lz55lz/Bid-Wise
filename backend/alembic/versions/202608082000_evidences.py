"""Create immutable document Evidence and its integrity guard."""

from alembic import op

revision = "202608082000"
down_revision = "202608081700"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create type app.evidence_source_type as enum (
          'DOCUMENT_TEXT', 'DOCUMENT_TABLE', 'DOCUMENT_IMAGE', 'DOCUMENT_SECTION',
          'USER_CONFIRMATION', 'SYSTEM_RULE'
        );

        create table app.evidences (
          id uuid primary key default gen_random_uuid(),
          source_type app.evidence_source_type not null,
          document_version_id uuid references app.document_versions(id) on delete restrict,
          document_node_id uuid references app.document_nodes(id) on delete restrict,
          page_number integer,
          quoted_text text,
          content_hash char(64),
          bbox jsonb,
          source_reference jsonb not null default '{}'::jsonb,
          confidence numeric(5,4),
          created_at timestamptz not null default now(),
          created_by uuid references app.users(id) on delete restrict,
          constraint ck_evidences_confidence check (
            confidence is null or confidence between 0 and 1
          ),
          constraint ck_evidences_document_source check (
            (source_type in ('DOCUMENT_TEXT', 'DOCUMENT_TABLE', 'DOCUMENT_IMAGE', 'DOCUMENT_SECTION')
              and document_version_id is not null and document_node_id is not null)
            or source_type in ('USER_CONFIRMATION', 'SYSTEM_RULE')
          )
        );

        create or replace function app.validate_evidence_node_version()
        returns trigger language plpgsql as $$
        begin
          if new.document_node_id is not null and not exists (
            select 1 from app.document_nodes node
            where node.id = new.document_node_id
              and node.document_version_id = new.document_version_id
          ) then
            raise exception 'evidence node must belong to the referenced document version';
          end if;
          return new;
        end;
        $$;
        create trigger trg_evidences_node_version
          before insert or update of document_version_id, document_node_id on app.evidences
          for each row execute function app.validate_evidence_node_version();

        create index ix_evidences_document_page
          on app.evidences (document_version_id, page_number);
    """)


def downgrade() -> None:
    op.execute("""
        drop trigger trg_evidences_node_version on app.evidences;
        drop function app.validate_evidence_node_version();
        drop table app.evidences;
        drop type app.evidence_source_type;
    """)
