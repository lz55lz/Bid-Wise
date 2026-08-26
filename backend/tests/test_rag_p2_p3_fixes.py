"""P2/P3 RAG 优化测试

覆盖：
  - P2-1 _rrf_fuse: query_type=FACTUAL 时 keyword 权重升到 0.5
  - P2-1 _rrf_fuse: 其他 query_type 保持 RetrievalConfig 默认权重
  - P2-1 _rrf_fuse: query_type=None 时走默认（向后兼容）
  - P2-2 _dynamic_context_limit: FACTUAL 返回 6，其他返回 RAG_CONTEXT_LIMIT
  - P3 _manual_knowledge_contexts: 已彻底删除
"""
from unittest.mock import MagicMock

import pytest

from app.core.retrieval_config import RetrievalConfig
from app.services.query_rewrite_service import QueryType


def _make_vector_hit(pk: str, score: float):
    """构造 VectorSearchHit mock。"""
    hit = MagicMock()
    hit.pk = pk
    hit.score = score
    return hit


def _make_bm25_hit(pk: str, content: str, rank: int):
    """构造 Bm25Hit mock。"""
    chunk = MagicMock()
    chunk.id = pk
    chunk.content = content
    hit = MagicMock()
    hit.chunk = chunk
    hit.rank = rank
    return hit


# -------------------------------------------------------------------
# P2-1 _rrf_fuse: 自适应权重
# -------------------------------------------------------------------


def test_rrf_fuse_factual_query_uses_higher_keyword_weight():
    """FACTUAL: vector/keyword 权重都是 0.5。"""
    from app.services.knowledge_rag_service import KnowledgeRagService

    cfg = RetrievalConfig(rrf_vector_weight=0.7, rrf_keyword_weight=0.3)
    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = cfg

    vector_hits = [
        _make_vector_hit("vec-1", 0.9),
        _make_vector_hit("vec-2", 0.7),
    ]
    # bm25 召回 vec-3，应在 FACTUAL 下被 keyword 权重抬升
    bm25_hits = [_make_bm25_hit("vec-3", "本规定自2017年10月1日起施行。", rank=0)]

    fused = service._rrf_fuse(
        vector_hits,
        bm25_hits,
        top_k=10,
        keyword_boost=False,
        query_type=QueryType.FACTUAL,
    )
    fused_map = dict(fused)
    # vec-3 (BM25 only): kw / (60 + 1) = 0.5 / 61 ≈ 0.00820
    # vec-1 (vector rank 0): vw / (60 + 1) = 0.5 / 61 ≈ 0.00820
    # vec-2 (vector rank 1): vw / (60 + 2) = 0.5 / 62 ≈ 0.00806
    # vec-3 与 vec-1 分数相同（按字典序）
    assert abs(fused_map["vec-3"] - 0.5 / 61) < 1e-6
    assert abs(fused_map["vec-1"] - 0.5 / 61) < 1e-6


def test_rrf_fuse_default_query_uses_config_weights():
    """非 FACTUAL: 走 RetrievalConfig 默认（0.7 / 0.3）。"""
    from app.services.knowledge_rag_service import KnowledgeRagService

    cfg = RetrievalConfig(rrf_vector_weight=0.7, rrf_keyword_weight=0.3)
    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = cfg

    vector_hits = [_make_vector_hit("vec-1", 0.9)]
    bm25_hits = [_make_bm25_hit("vec-2", "some content", rank=0)]

    fused = service._rrf_fuse(
        vector_hits,
        bm25_hits,
        top_k=10,
        keyword_boost=False,
        query_type=QueryType.DEFINITION,
    )
    fused_map = dict(fused)
    assert abs(fused_map["vec-1"] - 0.7 / 61) < 1e-6
    assert abs(fused_map["vec-2"] - 0.3 / 61) < 1e-6


def test_rrf_fuse_none_query_type_falls_back_to_config():
    """向后兼容: query_type=None 时不影响短路行为（仅 vector → 直接 score 排序）。

    query_type 影响的是 RRF 路径的权重；单通路短路不进入 RRF，所以权重无关。
    """
    from app.services.knowledge_rag_service import KnowledgeRagService

    cfg = RetrievalConfig(rrf_vector_weight=0.8, rrf_keyword_weight=0.2)
    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = cfg

    vector_hits = [_make_vector_hit("vec-1", 0.9)]
    bm25_hits = []

    fused = service._rrf_fuse(
        vector_hits, bm25_hits, top_k=10, keyword_boost=False, query_type=None
    )
    fused_map = dict(fused)
    # PR2 短路：直接返回 score
    assert fused_map["vec-1"] == 0.9


def test_rrf_fuse_keyword_boost_still_works_with_adaptive_weights():
    """FACTUAL + keyword_boost=True: 自适应权重 + keyword boost 同时生效。"""
    from app.services.knowledge_rag_service import KnowledgeRagService

    cfg = RetrievalConfig(keyword_boost_enabled=True)
    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = cfg

    vector_hits = [_make_vector_hit("vec-1", 0.9)]
    bm25_hits = [_make_bm25_hit("vec-2", "本规定自2017年10月1日起施行。", rank=0)]

    fused = service._rrf_fuse(
        vector_hits,
        bm25_hits,
        top_k=10,
        keyword_boost=True,
        query_type=QueryType.FACTUAL,
    )
    fused_map = dict(fused)
    # vec-2 至少有 2 个 keyword pattern 命中（年月 + 施行 + 日起...），
    # + boost ≥ 0.5 * matches
    base_score = 0.5 / 61  # keyword 路径基础分
    assert fused_map["vec-2"] > base_score


# -------------------------------------------------------------------
# P2-2 _dynamic_context_limit
# -------------------------------------------------------------------


def test_dynamic_context_limit_factual_returns_six():
    """FACTUAL: 6 条 context（精确答案，少而精）。"""
    from app.services.knowledge_rag_service import _dynamic_context_limit

    assert _dynamic_context_limit(QueryType.FACTUAL) == 6


def test_dynamic_context_limit_other_returns_default():
    """其他类型: 返回 RAG_CONTEXT_LIMIT（默认 8）。"""
    from app.core.constants import RAG_CONTEXT_LIMIT
    from app.services.knowledge_rag_service import _dynamic_context_limit

    assert _dynamic_context_limit(QueryType.DEFINITION) == RAG_CONTEXT_LIMIT
    assert _dynamic_context_limit(QueryType.LIST) == RAG_CONTEXT_LIMIT
    assert _dynamic_context_limit(QueryType.PROCEDURAL) == RAG_CONTEXT_LIMIT
    assert _dynamic_context_limit(QueryType.DEFAULT) == RAG_CONTEXT_LIMIT
    assert _dynamic_context_limit(QueryType.GREETING) == RAG_CONTEXT_LIMIT


def test_dynamic_context_limit_none_returns_default():
    """query_type=None 时 fallback RAG_CONTEXT_LIMIT。"""
    from app.core.constants import RAG_CONTEXT_LIMIT
    from app.services.knowledge_rag_service import _dynamic_context_limit

    assert _dynamic_context_limit(None) == RAG_CONTEXT_LIMIT


# -------------------------------------------------------------------
# P3 _manual_knowledge_contexts 已删除
# -------------------------------------------------------------------


def test_manual_knowledge_contexts_attribute_removed():
    """_manual_knowledge_contexts 方法应已彻底删除。"""
    from app.services import knowledge_rag_service

    service = knowledge_rag_service.KnowledgeRagService
    # 类自身不应有此方法
    assert not hasattr(service, "_manual_knowledge_contexts"), (
        "_manual_knowledge_contexts 死代码未清理"
    )
    # 实例化时也不应有此方法
    instance = service.__new__(service)
    assert not hasattr(instance, "_manual_knowledge_contexts")


# -------------------------------------------------------------------
# 集成：_aretrieve 把 query_type 传给 _rrf_fuse
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aretrieve_passes_query_type_to_rrf_fuse():
    """_aretrieve 调 _rrf_fuse 时传入 query_type=QueryType.FACTUAL。"""
    from app.services.knowledge_rag_service import KnowledgeRagService

    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = RetrievalConfig(
        vector_threshold=0.5, embedding_top_k=10, rerank_top_k=10
    )
    service._vector_store = MagicMock()
    service._search = MagicMock()

    service._vector_store.search_knowledge = MagicMock(
        return_value=[_make_vector_hit("vec-1", 0.9)]
    )
    service._search.search_chunks_bm25 = MagicMock(return_value=[])
    service._search.get_chunks_content_by_pks = MagicMock(return_value={})
    service._search.list_visible_knowledge_chunks = MagicMock(return_value=[])
    service._rrf_fuse = MagicMock(return_value=[("vec-1", 0.5)])
    service._logger = MagicMock()

    from app.services.query_rewrite_service import QueryRewriteResult

    rewrite = QueryRewriteResult(
        query_type=QueryType.FACTUAL,
        original_query="什么时候施行",
        expanded_query="什么时候施行",
        multi_queries=[],
        rerank_query="什么时候施行",
    )

    await service._aretrieve(
        question="什么时候施行",
        query_vector=[0.0] * 1024,
        project_id=None,
        actor_id=MagicMock(),
        role_codes=set(),
        rewrite_result=rewrite,
    )

    fuse_kwargs = service._rrf_fuse.call_args.kwargs
    assert "query_type" in fuse_kwargs
    assert fuse_kwargs["query_type"] == QueryType.FACTUAL


@pytest.mark.asyncio
async def test_aretrieve_no_evidence_manual_contexts_call():
    """P3: _aretrieve 不再调用 _manual_knowledge_contexts（已删除）。"""
    from app.services.knowledge_rag_service import KnowledgeRagService

    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = RetrievalConfig(
        vector_threshold=0.5, embedding_top_k=10, rerank_top_k=10
    )
    service._vector_store = MagicMock()
    service._search = MagicMock()

    service._vector_store.search_knowledge = MagicMock(
        return_value=[_make_vector_hit("vec-1", 0.9)]
    )
    service._search.search_chunks_bm25 = MagicMock(return_value=[])
    service._search.get_chunks_content_by_pks = MagicMock(return_value={})
    service._search.list_visible_knowledge_chunks = MagicMock(return_value=[])
    service._rrf_fuse = MagicMock(return_value=[("vec-1", 0.5)])
    service._logger = MagicMock()

    from app.services.query_rewrite_service import QueryRewriteResult

    rewrite = QueryRewriteResult(
        query_type=QueryType.DEFAULT,
        original_query="test",
        expanded_query="test",
        multi_queries=[],
        rerank_query="test",
    )

    # 调用不应抛 AttributeError（_manual_knowledge_contexts 已被删除）
    await service._aretrieve(
        question="test",
        query_vector=[0.0] * 1024,
        project_id=None,
        actor_id=MagicMock(),
        role_codes=set(),
        rewrite_result=rewrite,
    )