"""index 节点 — 为问答产品写 SearchChunk 向量索引

本节点调用 DocumentIndexingService：
  - 从 document_nodes 构建 chunk（父子块体系，含 section 上下文头）
  - BGE-M3 向量化写入 pgvector（app.search_chunks.embedding）
  - 生成 Evidence / 邻居链 /（LEGAL 才有的）FAQ

投标分析链路自身的召回不走这里（extract recall 直接在 document_nodes 上
做 BM25/关键词），本索引服务的是项目问答（RagService）和 IM 检索。
"""
from typing import Any

from app.services.bid_pipeline.state import BidState
from app.services.observability import stage_task


def _search_chunks_by_keyword(session, chunk_ids: list[str], keywords: list[str], limit: int = 20):
    """Use the shared Chinese BM25 query contract for index-side lookups."""
    from app.services.bid_pipeline.extract_subgraph import _search_bm25_by_keyword

    return _search_bm25_by_keyword(session, chunk_ids, keywords, limit)


@stage_task("index")
def index_node(state: BidState) -> dict[str, Any]:
    """写 SearchChunk 向量索引（问答产品的事实源）。"""
    version_id = state.get("version_id")
    if version_id is None:
        return {
            "current_stage": "index",
            "stage_status": {"index": "skipped"},
        }

    from app.core.config import get_settings
    from app.db.session import get_session_factory
    from app.integrations.ai.embedding import BgeM3Client
    from app.integrations.vector_store import PgVectorStore
    from app.services.document_indexing_service import DocumentIndexingService

    session = get_session_factory()()
    try:
        settings = get_settings()
        service = DocumentIndexingService(
            session,
            BgeM3Client(settings),
            PgVectorStore(settings),
        )
        chunks = service.do_index(version_id)
        return {
            "current_stage": "index",
            "stage_status": {"index": "done"},
            "indexed_chunks": len(chunks),
        }
    finally:
        session.close()
