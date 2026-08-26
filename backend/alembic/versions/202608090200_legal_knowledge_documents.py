"""Link legal knowledge versions to controlled document sources."""

from alembic import op

revision = "202608090200"
down_revision = "202608090100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL does not support removing enum labels, so downgrade leaves the
    # harmless LEGAL/CASE labels in place while reverting relational metadata.
    op.execute("alter type app.document_type add value if not exists 'LEGAL'")
    op.execute("alter type app.document_type add value if not exists 'CASE'")
    op.execute(
        """
        alter table app.knowledge_versions
        add column source_document_version_id uuid
        references app.document_versions(id)
        """
    )
    op.execute(
        """
        create unique index uq_knowledge_versions_source_document_version
        on app.knowledge_versions (source_document_version_id)
        where source_document_version_id is not null
        """
    )
    op.execute(
        """
        create index ix_knowledge_versions_source_document_version
        on app.knowledge_versions (source_document_version_id)
        where source_document_version_id is not null
        """
    )


def downgrade() -> None:
    op.execute("drop index if exists app.ix_knowledge_versions_source_document_version")
    op.execute("drop index if exists app.uq_knowledge_versions_source_document_version")
    op.execute(
        "alter table app.knowledge_versions drop column if exists source_document_version_id"
    )
