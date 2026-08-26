"""BM25 中文分词（zhparser）测试

覆盖：
  - _search_bm25_by_keyword: 'zh' text search config + websearch_to_tsquery
  - _search_bm25_by_keyword: 空关键词清洗
  - _search_bm25_by_keyword: tsvector 失败回退 ILIKE
  - _search_chunks_by_keyword: 同步切到 'zh' / websearch_to_tsquery
"""
from unittest.mock import MagicMock


def _captured_sql(mock_session: MagicMock) -> str:
    """取 session.execute 最近一次调用的 SQL 文本（含 f-string 拼接的 chunk_ids）。"""
    call_args = mock_session.execute.call_args
    # call_args.args 是 (text_obj, params) 或仅 text_obj
    text_obj = call_args.args[0]
    return str(text_obj)


def _captured_params(mock_session: MagicMock) -> dict:
    call_args = mock_session.execute.call_args
    return call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("params", {})


# -------------------------------------------------------------------
# _search_bm25_by_keyword
# -------------------------------------------------------------------


def test_search_bm25_by_keyword_uses_zh_config():
    """改 simple → zh 后 SQL 必须用 'zh' parser 且不含 'simple'。"""
    from app.services.bid_pipeline.extract_subgraph import _search_bm25_by_keyword

    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = [(1, 0.5), (2, 0.3)]

    result = _search_bm25_by_keyword(mock_session, [1, 2, 3], ["人民银行", "招标"], limit=20)

    sql = _captured_sql(mock_session)
    assert "to_tsvector('zh'" in sql
    assert "'simple'" not in sql
    assert result == [(1, 0.5), (2, 0.3)]


def test_search_bm25_uses_websearch_to_tsquery():
    """用 websearch_to_tsquery 替代 to_tsquery，避免中文标点 tsquery syntax error。"""
    from app.services.bid_pipeline.extract_subgraph import _search_bm25_by_keyword

    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = []

    _search_bm25_by_keyword(mock_session, [1], ["中国人民银行（2017）", "87号令"], limit=10)

    sql = _captured_sql(mock_session)
    assert "websearch_to_tsquery('zh'" in sql
    # 用 word-boundary 排除 websearch_to_tsquery 的 substring 干扰
    import re
    assert re.search(r"\bto_tsquery\s*\(", sql) is None, (
        "应使用 websearch_to_tsquery 而非裸 to_tsquery"
    )
    params = _captured_params(mock_session)
    assert "kw_query" in params
    assert " OR " in params["kw_query"]


def test_search_bm25_drops_empty_keywords_returns_empty():
    """全空关键词应清洗后直接返回空，不抛错。"""
    from app.services.bid_pipeline.extract_subgraph import _search_bm25_by_keyword

    mock_session = MagicMock()

    result = _search_bm25_by_keyword(mock_session, [1, 2], ["", "  ", None], limit=10)

    assert result == []
    mock_session.execute.assert_not_called()


def test_search_bm25_returns_empty_when_no_chunk_ids():
    """chunk_ids 为空 → 直接返回空，不查 DB。"""
    from app.services.bid_pipeline.extract_subgraph import _search_bm25_by_keyword

    mock_session = MagicMock()

    result = _search_bm25_by_keyword(mock_session, [], ["中国人民银行"], limit=10)

    assert result == []
    mock_session.execute.assert_not_called()


def test_search_bm25_falls_back_to_ilike_on_failure():
    """tsvector 查询失败（如 PG 报错）应回退 ILIKE 模糊匹配。"""
    from app.services.bid_pipeline.extract_subgraph import _search_bm25_by_keyword

    mock_session = MagicMock()
    # 第一次 execute（tsvector）抛错，第二次（ILIKE fallback）返回结果
    mock_session.execute.side_effect = [
        Exception("tsvector syntax error"),
        MagicMock(fetchall=MagicMock(return_value=[(10, 1.0)])),
    ]

    result = _search_bm25_by_keyword(mock_session, [10], ["中国人民银行"], limit=10)

    assert result == [(10, 1.0)]
    assert mock_session.execute.call_count == 2
    # 第二次调用走 ILIKE 分支
    fallback_sql = _captured_sql(mock_session)
    assert "ILIKE" in fallback_sql
    assert "to_tsvector" not in fallback_sql


def test_search_bm25_uses_or_semantics():
    """关键词用 ' OR ' 连接（任一命中即召回，比原 AND 召回率高）。"""
    from app.services.bid_pipeline.extract_subgraph import _search_bm25_by_keyword

    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = []

    _search_bm25_by_keyword(mock_session, [1], ["人民银行", "招标"], limit=10)

    params = _captured_params(mock_session)
    assert params["kw_query"] == "人民银行 OR 招标"


def test_search_bm25_strips_whitespace_in_keywords():
    """关键词首尾空白应被 strip 掉。"""
    from app.services.bid_pipeline.extract_subgraph import _search_bm25_by_keyword

    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = []

    _search_bm25_by_keyword(mock_session, [1], ["  人民银行  ", "招标"], limit=10)

    params = _captured_params(mock_session)
    assert params["kw_query"] == "人民银行 OR 招标"


# -------------------------------------------------------------------
# _search_chunks_by_keyword（index_node）
# -------------------------------------------------------------------


def test_search_chunks_by_keyword_uses_zh_config():
    """index_node 同步切到 'zh' / websearch_to_tsquery。"""
    from app.services.bid_pipeline.index_node import _search_chunks_by_keyword

    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = [(1, 0.5)]

    result = _search_chunks_by_keyword(mock_session, [1, 2], ["人民银行"], limit=10)

    sql = _captured_sql(mock_session)
    assert "to_tsvector('zh'" in sql
    assert "'simple'" not in sql
    assert "websearch_to_tsquery('zh'" in sql
    assert result == [(1, 0.5)]


def test_search_chunks_by_keyword_drops_empty_keywords():
    """index_node 也走空关键词清洗。"""
    from app.services.bid_pipeline.index_node import _search_chunks_by_keyword

    mock_session = MagicMock()

    result = _search_chunks_by_keyword(mock_session, [1], ["", "  "], limit=10)

    assert result == []
    mock_session.execute.assert_not_called()
