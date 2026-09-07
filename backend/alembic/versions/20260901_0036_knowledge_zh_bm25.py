"""为已发布法规/案例知识启用 zhparser BM25 检索。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260901_0036"
down_revision: str | Sequence[str] | None = "20260901_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_zh_bm25
        ON knowledge_chunks USING gin (to_tsvector('zh', content));
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_zh_bm25")
