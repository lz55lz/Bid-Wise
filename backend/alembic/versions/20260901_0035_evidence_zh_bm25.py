"""为项目 Evidence 启用 zhparser BM25 检索。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260901_0035"
down_revision: str | Sequence[str] | None = "20260901_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS zhparser")
    op.execute(
        """
        DO $$
        DECLARE token_types text;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'zh') THEN
                CREATE TEXT SEARCH CONFIGURATION zh (PARSER = zhparser);
                SELECT string_agg(alias, ',' ORDER BY alias)
                  INTO token_types FROM ts_token_type('zhparser');
                EXECUTE format(
                  'ALTER TEXT SEARCH CONFIGURATION zh ADD MAPPING FOR %s WITH simple',
                  token_types
                );
            END IF;
        END$$;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_evidences_zh_bm25
        ON evidences USING gin (to_tsvector('zh', coalesce(quoted_text, '')))
        WHERE source_type = 'DOCUMENT_NODE';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_evidences_zh_bm25")
