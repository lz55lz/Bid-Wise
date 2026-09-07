"""对齐结构化 RAG 的 JSONB 字段与知识 BM25 索引。\n\nRevision ID: 20260903_0042\nRevises: 20260903_0041\n"""

from collections.abc import Sequence

from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260903_0042"
down_revision: str | Sequence[str] | None = "20260903_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0030 创建这些列时仍使用 JSON；ORM 与后续检索统一使用 JSONB。
    op.alter_column(
        "document_versions",
        "cleaning_summary",
        existing_type=postgresql.JSON(),
        type_=postgresql.JSONB(),
        postgresql_using="cleaning_summary::jsonb",
    )
    op.alter_column(
        "document_nodes",
        "cleaning_metadata",
        existing_type=postgresql.JSON(),
        type_=postgresql.JSONB(),
        postgresql_using="cleaning_metadata::jsonb",
    )
    op.alter_column(
        "document_nodes",
        "bbox",
        existing_type=postgresql.JSON(),
        type_=postgresql.JSONB(),
        postgresql_using="bbox::jsonb",
    )

    # section_path 与正文共同参与法规/案例词法召回，且索引表达式与 Repository 完全一致。
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_zh_bm25")
    op.execute(
        """
        CREATE INDEX ix_knowledge_chunks_zh_bm25
        ON knowledge_chunks USING gin (
          to_tsvector('zh'::regconfig, coalesce(section_path, '') || ' ' || content)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_zh_bm25")
    op.execute(
        """
        CREATE INDEX ix_knowledge_chunks_zh_bm25
        ON knowledge_chunks USING gin (to_tsvector('zh'::regconfig, content));
        """
    )
    op.alter_column(
        "document_nodes",
        "bbox",
        existing_type=postgresql.JSONB(),
        type_=postgresql.JSON(),
        postgresql_using="bbox::json",
    )
    op.alter_column(
        "document_nodes",
        "cleaning_metadata",
        existing_type=postgresql.JSONB(),
        type_=postgresql.JSON(),
        postgresql_using="cleaning_metadata::json",
    )
    op.alter_column(
        "document_versions",
        "cleaning_summary",
        existing_type=postgresql.JSONB(),
        type_=postgresql.JSON(),
        postgresql_using="cleaning_summary::json",
    )
