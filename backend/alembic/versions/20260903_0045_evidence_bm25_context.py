"""让项目 Evidence 的 BM25 与结构化检索文本保持一致。

Revision ID: 20260903_0045
Revises: 20260903_0044
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0045"
down_revision: str | Sequence[str] | None = "20260903_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 文档名与完整 section_path 能显著提高“某附件/某章节/第几条”这类精确词法召回。
    # 表达式必须和 EvidenceRepository.list_bm25_search_candidates 完全一致，
    # 否则 PostgreSQL 无法使用函数 GIN 索引。
    op.execute("DROP INDEX IF EXISTS ix_evidences_zh_bm25")
    op.execute(
        """
        CREATE INDEX ix_evidences_zh_bm25
        ON evidences USING gin (
          to_tsvector(
            'zh'::regconfig,
            coalesce(locator ->> 'document_name', '') || ' ' ||
            coalesce(locator ->> 'section_path', '') || ' ' ||
            coalesce(quoted_text, '')
          )
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_evidences_zh_bm25")
    op.execute(
        """
        CREATE INDEX ix_evidences_zh_bm25
        ON evidences USING gin (
          to_tsvector('zh'::regconfig, coalesce(quoted_text, ''))
        );
        """
    )
