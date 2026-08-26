"""Mark bid_doc_chunk as deprecated.

背景：
  bid_doc_chunk 是 bid_pipeline 历史遗留的 chunk 存储表。
  现状是 bid_pipeline 同时写入两张表：
    - document_nodes（PR4 _parse_node 改后写入，被 search_chunks.source_node_id 引用）
    - bid_doc_chunk（clean_node / annotate_node / index_node 写入，无外部消费者）

  RAG 检索只读 SearchChunk 表（document_nodes → search_chunks），完全不读 bid_doc_chunk。
  bid_doc_chunk 是孤儿表。

本次迁移：
  - 给 bid_doc_chunk 加 deprecated_at 列（标记而非删除，给清理留窗口期）
  - 不删表（避免线上数据丢失）
  - 不删索引（保留性能，等真清理时一起处理）

后续清理（不在本次 PR）：
  - 确认 production 数据已迁移到 document_nodes 后，删除 bid_doc_chunk 表 + 4 个节点的写入代码
"""

from alembic import op

revision = "202609020000"
down_revision = "202609010000"


def upgrade() -> None:
    # 本地库可能由 run_bid_schema_clean.sql 直接建表（不含已废弃的 bid_doc_chunk），需守卫
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('bid_doc_chunk') IS NOT NULL THEN
                ALTER TABLE bid_doc_chunk ADD COLUMN IF NOT EXISTS deprecated_at TIMESTAMPTZ;
                COMMENT ON COLUMN bid_doc_chunk.deprecated_at IS
                    '标记此表已被 document_nodes 取代；保留期结束后由后续迁移删除。';
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE bid_doc_chunk DROP COLUMN IF EXISTS deprecated_at")