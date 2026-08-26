"""Add immutable raw-to-clean document processing state."""

from alembic import op

revision = "202608090400"
down_revision = "202608090300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter type app.parse_status add value if not exists 'CLEANING'")
    op.execute("alter type app.task_type add value if not exists 'CLEAN_DOCUMENT'")
    # LEGAL enum value was added by 202608090200 but not committed before use.
    # Force a commit before using it in DDL.
    op.execute("commit")
    op.execute("alter table app.documents drop constraint if exists ck_documents_scope")
    op.execute(
        """
        alter table app.documents add constraint ck_documents_scope check (
            (document_type = 'TENDER' and project_id is not null)
            or (document_type in ('ENTERPRISE', 'LEGAL', 'CASE') and project_id is null)
        )
        """
    )
    op.execute("alter table app.document_versions add column cleaning_summary jsonb")
    op.execute("alter table app.document_nodes add column cleaned_content text")
    op.execute(
        """
        alter table app.document_nodes add column cleaning_metadata jsonb not null default '{}'::jsonb
        """
    )
    # Retain existing searchable documents while making their pre-cleaning
    # provenance explicit. Newly uploaded files always go through CLEAN_DOCUMENT.
    op.execute(
        """
        update app.document_nodes
        set cleaned_content = nullif(regexp_replace(btrim(content), '\\s+', ' ', 'g'), ''),
            cleaning_metadata = jsonb_build_object(
                'indexable', length(btrim(content)) >= 8,
                'legacy_backfill', true,
                'flags', '[]'::jsonb
            )
        where cleaned_content is null
        """
    )
    op.execute(
        """
        update app.document_versions
        set cleaning_summary = jsonb_build_object('legacy_backfill', true)
        where cleaning_summary is null and parse_status = 'READY'
        """
    )


def downgrade() -> None:
    op.execute("alter table app.document_nodes drop column if exists cleaning_metadata")
    op.execute("alter table app.document_nodes drop column if exists cleaned_content")
    op.execute("alter table app.document_versions drop column if exists cleaning_summary")
    # Enum labels remain because PostgreSQL cannot safely remove them.
