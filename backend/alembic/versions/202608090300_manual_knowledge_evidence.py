"""Add auditable evidence links for manually maintained knowledge."""

from alembic import op

revision = "202608090300"
down_revision = "202608090200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        alter table app.knowledge_versions
        add column source_evidence_id uuid references app.evidences(id)
        """
    )
    op.execute(
        """
        create index ix_knowledge_versions_source_evidence
        on app.knowledge_versions (source_evidence_id)
        where source_evidence_id is not null
        """
    )
    op.execute(
        """
        insert into app.evidences (
            id, source_type, document_version_id, document_node_id, page_number,
            quoted_text, content_hash, bbox, source_reference, confidence,
            created_at, created_by
        )
        select
            gen_random_uuid(), 'USER_CONFIRMATION', null, null, null,
            left(kv.content, 1000), null, null,
            jsonb_build_object(
                'knowledge_entry_id', ke.id::text,
                'knowledge_version_id', kv.id::text,
                'source_reference', ke.source_reference
            ),
            null, kv.created_at, kv.created_by
        from app.knowledge_versions kv
        join app.knowledge_entries ke on ke.id = kv.knowledge_entry_id
        where kv.source_document_version_id is null
          and kv.source_evidence_id is null
        """
    )
    op.execute(
        """
        update app.knowledge_versions kv
        set source_evidence_id = e.id
        from app.evidences e
        where kv.source_document_version_id is null
          and kv.source_evidence_id is null
          and e.source_type = 'USER_CONFIRMATION'
          and e.source_reference ->> 'knowledge_version_id' = kv.id::text
        """
    )


def downgrade() -> None:
    op.execute("drop index if exists app.ix_knowledge_versions_source_evidence")
    op.execute("alter table app.knowledge_versions drop column if exists source_evidence_id")
