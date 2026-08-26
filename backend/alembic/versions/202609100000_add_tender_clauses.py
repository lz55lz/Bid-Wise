"""Add clause-level document facts for tender understanding.

Document nodes remain the parser-compatible layout layer.  Clauses are a
derived, version-scoped business layer consumed by requirement extraction and
report citations.
"""

from alembic import op


revision = "202609100000"
down_revision = "202609090000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create table app.tender_clauses (
          id uuid primary key,
          document_version_id uuid not null references app.document_versions(id) on delete restrict,
          order_no integer not null,
          clause_type varchar(32) not null,
          section_path varchar(1024),
          start_page integer,
          end_page integer,
          content text not null,
          contextualized_content text not null,
          content_hash varchar(64) not null,
          mandatory_signal boolean not null default false,
          quality_metadata jsonb not null default '{}'::jsonb,
          created_at timestamptz not null,
          unique (document_version_id, order_no)
        );
        create index ix_tender_clauses_version_order on app.tender_clauses(document_version_id, order_no);
        create table app.clause_evidences (
          clause_id uuid not null references app.tender_clauses(id) on delete restrict,
          evidence_id uuid not null references app.evidences(id) on delete restrict,
          relation varchar(32) not null,
          primary key (clause_id, evidence_id)
        );
        """
    )


def downgrade() -> None:
    op.execute("drop table if exists app.clause_evidences")
    op.execute("drop table if exists app.tender_clauses")
