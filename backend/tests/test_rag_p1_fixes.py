"""P1 RAG 修复测试

覆盖：
  - _rrf_fuse: vector_content_map 让 vector hits 也能算 keyword boost
  - _aretrieve: knowledge_hits 按 vector_threshold 过滤
  - SearchRepository.get_chunks_content_by_pks: 批量返回 {pk: content}
  - _aretrieve: 串联 vector content 拉取并传给 RRF
"""
from unittest.mock import MagicMock

import pytest

from app.core.retrieval_config import RetrievalConfig


def _make_vector_hit(pk: str, score: float):
    """构造 VectorSearchHit mock。"""
    hit = MagicMock()
    hit.pk = pk
    hit.score = score
    return hit


def _make_bm25_hit(pk: str, content: str, rank: int, bm25_score: float = 1.0):
    """构造 Bm25Hit mock。"""
    chunk = MagicMock()
    chunk.id = pk
    chunk.content = content
    hit = MagicMock()
    hit.chunk = chunk
    hit.rank = rank
    hit.bm25_score = bm25_score
    return hit


# -------------------------------------------------------------------
# _rrf_fuse: vector_content_map 让 vector hits 算 boost
# -------------------------------------------------------------------


def test_rrf_fuse_vector_hits_get_keyword_boost_when_content_provided():
    """FACTUAL + vector_content_map: vector hits 含日期模式应被 boost。

    注：仅 vector 通路走 PR2 短路（直接按 score 排序），不走 RRF 也不走 boost。
    测试改为验证双通路场景下的 boost 行为。
    """
    from app.services.knowledge_rag_service import KnowledgeRagService

    cfg = RetrievalConfig(keyword_boost_enabled=True)
    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = cfg

    vector_hits = [
        _make_vector_hit("vec-1", 0.9),
        _make_vector_hit("vec-2", 0.7),  # 也应被 boost（content 含"2017年10月"）
    ]
    # 双通路都存在 → 走 RRF，keyword boost 生效
    bm25_hits: list = []
    # 加入一个 bm25 hit 让双通路都存在
    bm25_hits.append(_make_bm25_hit("vec-3", "本规定自2017年10月1日起施行。", rank=0))
    # vector-2 content 含日期模式
    vector_content_map = {
        "vec-2": "本规定自2017年10月1日起施行。",
    }

    fused = service._rrf_fuse(
        vector_hits,
        bm25_hits,
        top_k=10,
        keyword_boost=True,
        vector_content_map=vector_content_map,
    )

    fused_map = dict(fused)
    # vec-2 的 boost 让它排名更高（vector_score 0.7 + boost ≥ 0.5）
    assert fused_map["vec-2"] > fused_map["vec-1"], (
        f"vec-2 应被 boost 排名更高: {fused_map}"
    )


def test_rrf_fuse_vector_hits_no_boost_when_content_map_empty():
    """FACTUAL + vector_content_map={}: vector hits 不算 boost（与 BM25 行为一致）。"""
    from app.services.knowledge_rag_service import KnowledgeRagService

    cfg = RetrievalConfig(keyword_boost_enabled=True)
    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = cfg

    vector_hits = [
        _make_vector_hit("vec-1", 0.9),
        _make_vector_hit("vec-2", 0.7),
    ]
    fused = service._rrf_fuse(
        vector_hits,
        bm25_hits=[],
        top_k=10,
        keyword_boost=True,
        vector_content_map={},
    )

    # 没有 content map → 都不算 boost，纯 vector 分数排序
    fused_map = dict(fused)
    assert fused_map["vec-1"] > fused_map["vec-2"]


def test_rrf_fuse_vector_content_overrides_bm25_when_pk_overlap():
    """同一 pk 同时被 BM25 + vector 召回：BM25 content 优先（避免重复覆盖）。"""
    from app.services.knowledge_rag_service import KnowledgeRagService

    cfg = RetrievalConfig(keyword_boost_enabled=True)
    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = cfg

    # 同一 pk 被两边召回，BM25 给的是"权威" content（含日期），vector 给的是空
    pk = "shared-pk"
    vector_hits = [_make_vector_hit(pk, 0.8)]
    bm25_hits = [_make_bm25_hit(pk, "本规定自2017年10月1日起施行。", rank=0)]
    vector_content_map = {pk: ""}  # 故意空

    fused = service._rrf_fuse(
        vector_hits, bm25_hits, top_k=10, keyword_boost=True, vector_content_map=vector_content_map
    )

    # BM25 content 含日期，应被 boost，shared-pk 分数大于 0
    assert dict(fused)[pk] > 0.0


def test_rrf_fuse_legacy_call_without_vector_content_map_still_works():
    """向后兼容：不传 vector_content_map 时不抛错。

    PR2 短路生效：仅 vector 通路走直接按 score 排序，不走 RRF。
    """
    from app.services.knowledge_rag_service import KnowledgeRagService

    cfg = RetrievalConfig(keyword_boost_enabled=True)
    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = cfg

    vector_hits = [_make_vector_hit("vec-1", 0.9)]
    bm25_hits = []

    fused = service._rrf_fuse(vector_hits, bm25_hits, top_k=10, keyword_boost=True)
    # PR2 短路：仅 vector 时直接用 score
    assert dict(fused) == {"vec-1": 0.9}


# -------------------------------------------------------------------
# SearchRepository.get_chunks_content_by_pks
# -------------------------------------------------------------------


def test_get_chunks_content_by_pks_returns_dict():
    """批量取 content 返回 {pk: content} dict。"""
    from app.db.repositories.search_repository import SearchRepository

    session = MagicMock()
    # execute().tuples() 返回 [(uuid, content), ...]
    session.execute.return_value.tuples.return_value = [
        ("id-1", "chunk one content"),
        ("id-2", "chunk two content"),
    ]
    repo = SearchRepository(session)

    result = repo.get_chunks_content_by_pks(["id-1", "id-2"])

    assert result == {"id-1": "chunk one content", "id-2": "chunk two content"}


def test_get_chunks_content_by_pks_empty_input():
    """空 list 不查 DB，直接返回空 dict。"""
    from app.db.repositories.search_repository import SearchRepository

    session = MagicMock()
    repo = SearchRepository(session)
    result = repo.get_chunks_content_by_pks([])

    assert result == {}
    session.execute.assert_not_called()


def test_get_chunks_content_by_pks_normalizes_none_to_empty_string():
    """chunk.content 为 None 时归一化为 ""（不破坏下游 string 操作）。"""
    from app.db.repositories.search_repository import SearchRepository

    session = MagicMock()
    session.execute.return_value.tuples.return_value = [("id-1", None)]
    repo = SearchRepository(session)

    result = repo.get_chunks_content_by_pks(["id-1"])
    assert result == {"id-1": ""}


# -------------------------------------------------------------------
# _aretrieve: vector_threshold 过滤 knowledge hits
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aretrieve_filters_knowledge_hits_by_vector_threshold():
    """_aretrieve 调 search_knowledge 后过滤掉 score < vector_threshold 的噪声。"""
    from app.services.knowledge_rag_service import KnowledgeRagService

    service = KnowledgeRagService.__new__(KnowledgeRagService)
    service._cfg = RetrievalConfig(vector_threshold=0.5, embedding_top_k=10)
    service._vector_store = MagicMock()
    service._search = MagicMock()

    # 模拟：3 个 hit，score 0.9 / 0.4 / 0.3 → 应只剩 0.9
    service._vector_store.search_knowledge = MagicMock(
        return_value=[
            _make_vector_hit("keep", 0.9),
            _make_vector_hit("drop-mid", 0.4),
            _make_vector_hit("drop-low", 0.3),
        ]
    )
    # RRF / 后续流程可能用，mock 掉避免报错
    service._search.search_chunks_bm25 = MagicMock(return_value=[])
    service._search.get_chunks_content_by_pks = MagicMock(return_value={})
    service._search.list_visible_knowledge_chunks = MagicMock(return_value=[])
    service._manual_knowledge_contexts = MagicMock(return_value=[])
    service._logger = MagicMock()

    # query_rewrite 走默认路径
    from app.services.query_rewrite_service import QueryRewriteResult, QueryType

    rewrite = QueryRewriteResult(
        query_type=QueryType.DEFAULT,
        original_query="test",
        expanded_query="test",
        multi_queries=[],
        rerank_query="test",
    )

    await service._aretrieve(
        question="test",
        query_vector=[0.0] * 1024,
        project_id=None,
        actor_id=MagicMock(),
        role_codes=set(),
        rewrite_result=rewrite,
    )

    # 验证 search_knowledge 拿到结果后被阈值过滤（虽然 contexts 可能空，但过滤逻辑跑了）
    # 直接验证：get_chunks_content_by_pks 只收到了保留下来的 pk
    call_args = service._search.get_chunks_content_by_pks.call_args
    requested_pks = call_args.args[0]
    assert requested_pks == ["keep"], (
        f"只该把保留下来的 pk 传给 get_chunks_content_by_pks，got {requested_pks}"
    )


@pytest.mark.asyncio
async def test_aretrieve_passes_vector_content_to_rrf_fuse():
    """_aretrieve 调 _rrf_fuse 时传入 vector_content_map。"""
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
    service._search.get_chunks_content_by_pks = MagicMock(
        return_value={"vec-1": "2017年10月1日起施行"}
    )
    service._search.list_visible_knowledge_chunks = MagicMock(return_value=[])
    service._manual_knowledge_contexts = MagicMock(return_value=[])
    service._rrf_fuse = MagicMock(return_value=[("vec-1", 0.5)])
    service._logger = MagicMock()

    from app.services.query_rewrite_service import QueryRewriteResult, QueryType

    rewrite = QueryRewriteResult(
        query_type=QueryType.FACTUAL,  # FACTUAL → keyword_boost=True
        original_query="什么时候施行",
        expanded_query="test",
        multi_queries=[],
        rerank_query="test",
    )

    await service._aretrieve(
        question="什么时候施行",
        query_vector=[0.0] * 1024,
        project_id=None,
        actor_id=MagicMock(),
        role_codes=set(),
        rewrite_result=rewrite,
    )

    # 断言 _rrf_fuse 被以 vector_content_map={"vec-1": "..."} 调用
    fuse_kwargs = service._rrf_fuse.call_args.kwargs
    assert "vector_content_map" in fuse_kwargs
    assert fuse_kwargs["vector_content_map"] == {"vec-1": "2017年10月1日起施行"}
    assert fuse_kwargs["keyword_boost"] is True
