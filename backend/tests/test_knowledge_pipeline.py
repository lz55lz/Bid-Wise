"""knowledge_pipeline 测试

覆盖：
  - parse_document_node：调用 document_ingest 解析 PDF（mock 真实文件）
  - embed_node：mock BgeM3Client，写 SearchChunk + 串联邻居链
  - publish_node：写 KnowledgeEntry + KnowledgeVersion + Evidence 关联
  - ingest_knowledge_document：整条链路，校验参数传递
"""
from unittest.mock import MagicMock, patch

import pytest

from app.services.knowledge_pipeline import (
    KnowledgeIngestResult,
    ingest_knowledge_document,
)


@pytest.fixture
def mock_session_factory():
    """Mock get_session_factory，返回可记录执行的 session。"""
    sessions = []

    def factory():
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        sessions.append(session)
        return session

    return factory, sessions


@pytest.fixture
def sample_chunks():
    return [
        {
            "chunk_id": "doc-1_0",
            "doc_id": "doc-1",
            "chunk_index": 0,
            "chunk_text": "本规定自2017年10月1日起施行。",
            "chunk_type": "paragraph",
        },
        {
            "chunk_id": "doc-1_1",
            "doc_id": "doc-1",
            "chunk_index": 1,
            "chunk_text": "招标投标法实施条例第四十条明确规定。",
            "chunk_type": "paragraph",
        },
    ]


# -------------------------------------------------------------------
# parse_document_node
# -------------------------------------------------------------------


def test_parse_document_node_calls_document_ingest():
    """parse_document_node 应委托给 document_ingest.parse_document。"""
    from app.services.knowledge_pipeline.pipeline import parse_document_node

    with patch("app.services.knowledge_pipeline.pipeline.parse_document") as mock_parse:
        mock_parse.return_value = ([{"chunk_text": "hello"}], "raw text")

        chunks = parse_document_node(
            file_path="/tmp/test.pdf",
            mime_type="application/pdf",
            document_version_id="11111111-1111-1111-1111-111111111111",
        )
        assert len(chunks) == 1
        assert chunks[0]["chunk_text"] == "hello"
        # 调用 document_ingest 时 doc_id 用 version_id 字符串
        args = mock_parse.call_args.args
        assert args[0] == "/tmp/test.pdf"
        assert args[1] == "application/pdf"
        assert args[2] == "11111111-1111-1111-1111-111111111111"


def test_parse_document_node_returns_empty_on_parse_failure():
    """parse_document 失败时返回空列表（不抛错）。"""
    from app.services.knowledge_pipeline.pipeline import parse_document_node

    with patch("app.services.knowledge_pipeline.pipeline.parse_document") as mock_parse:
        mock_parse.return_value = ([], "")  # 全部 parser 都失败

        chunks = parse_document_node(
            file_path="/nonexistent.pdf",
            mime_type="application/pdf",
            document_version_id="22222222-2222-2222-2222-222222222222",
        )
        assert chunks == []


# -------------------------------------------------------------------
# embed_node
# -------------------------------------------------------------------


def test_embed_node_validates_chunk_type():
    """embed_node 仅接受 LEGAL/CASE 两种 chunk_type。"""
    from app.services.knowledge_pipeline.pipeline import embed_node

    with pytest.raises(ValueError, match="chunk_type must be LEGAL or CASE"):
        embed_node(
            chunks=[{"chunk_text": "test"}],
            document_version_id="33333333-3333-3333-3333-333333333333",
            chunk_type="TENDER",  # bid_pipeline 专属，应被拒
        )


def test_embed_node_writes_chunks_to_search_table(sample_chunks):
    """embed_node 应对每个 chunk 插入 search_chunks 表 + 串联邻居链。"""
    from app.services.knowledge_pipeline.pipeline import embed_node

    # Mock session
    mock_session = MagicMock()
    # fetchone for chunk content lookup (publish_node 用，embed_node 不调)
    mock_session.execute.return_value.fetchone.return_value = ("dummy content",)

    with patch(
        "app.services.knowledge_pipeline.pipeline.get_session_factory"
    ) as mock_factory, patch(
        "app.integrations.ai.embedding.BgeM3Client"
    ) as mock_client_cls:
        mock_factory.return_value = MagicMock(return_value=mock_session)
        mock_client = MagicMock()
        # 每个 chunk 返回 1024 维零向量
        mock_client.embed.return_value = [[0.0] * 1024, [0.0] * 1024]
        mock_client_cls.return_value = mock_client

        chunk_ids = embed_node(
            chunks=sample_chunks,
            document_version_id="44444444-4444-4444-4444-444444444444",
            chunk_type="LEGAL",
        )

    assert len(chunk_ids) == 2
    # 验证每个 chunk 都执行了 INSERT
    insert_calls = [
        c for c in mock_session.execute.call_args_list
        if "INSERT INTO app.search_chunks" in str(c.args[0])
    ]
    assert len(insert_calls) == 2
    # chunk_type 应为 LEGAL（params 作为 positional args[1] 传入）
    for call in insert_calls:
        params = call.args[1] if len(call.args) > 1 else call.kwargs
        assert params["chunk_type"] == "LEGAL"
    # 验证 commit 被调用
    mock_session.commit.assert_called_once()


def test_embed_node_chains_neighbor_chunks(sample_chunks):
    """embed_node 应给 chunk 串联 pre_chunk_id / next_chunk_id。"""
    from app.services.knowledge_pipeline.pipeline import embed_node

    mock_session = MagicMock()

    with patch(
        "app.services.knowledge_pipeline.pipeline.get_session_factory"
    ) as mock_factory, patch(
        "app.integrations.ai.embedding.BgeM3Client"
    ) as mock_client_cls:
        mock_factory.return_value = MagicMock(return_value=mock_session)
        mock_client_cls.return_value.embed.return_value = [[0.0] * 1024, [0.0] * 1024]

        embed_node(
            chunks=sample_chunks,
            document_version_id="55555555-5555-5555-5555-555555555555",
            chunk_type="CASE",
        )

    # 验证同一批量 UPDATE 同时维护 next/pre 邻居链。
    next_calls = [
        c for c in mock_session.execute.call_args_list
        if "next_chunk_id" in str(c.args[0]) and "SET next" in str(c.args[0])
    ]
    # 2 个 chunk → 1 对邻居链接
    assert len(next_calls) == 1
    assert "pre_chunk_id" in str(next_calls[0].args[0])


# -------------------------------------------------------------------
# publish_node
# -------------------------------------------------------------------


def test_publish_node_updates_uploaded_draft_version():
    """publish_node 只能回写上传时创建的草稿版本，不能创建重复条目。"""
    from app.services.knowledge_pipeline.pipeline import publish_node

    mock_session = MagicMock()
    version = MagicMock()
    version.id = "77777777-7777-7777-7777-777777777777"
    version.knowledge_entry_id = "66666666-6666-6666-6666-666666666666"
    version.source_document_version_id = "66666666-6666-6666-6666-666666666666"
    version.status = "DRAFT"
    mock_session.get.return_value = version

    with patch(
        "app.services.knowledge_pipeline.pipeline.get_session_factory"
    ) as mock_factory:
        mock_factory.return_value = MagicMock(return_value=mock_session)

        entry_id, version_id = publish_node(
            document_version_id="66666666-6666-6666-6666-666666666666",
            knowledge_version_id="77777777-7777-7777-7777-777777777777",
            chunks=[{"chunk_text": "法规条款内容。"}],
            chunk_ids=["88888888-8888-8888-8888-888888888888"],
            chunk_type="LEGAL",
            actor_id="99999999-9999-9999-9999-999999999999",
        )

    # 只新增 DocumentNode + Evidence，不新增 KnowledgeEntry/KnowledgeVersion。
    add_calls = mock_session.add.call_args_list
    assert len(add_calls) == 2
    # commit 被调用一次
    mock_session.commit.assert_called_once()
    # 返回的是 UUID 对象
    assert entry_id is not None
    assert version_id is not None


def test_publish_node_rejects_invalid_chunk_type():
    """publish_node 仅接受 LEGAL/CASE。"""
    from app.services.knowledge_pipeline.pipeline import publish_node

    with pytest.raises(ValueError, match="chunk_type must be LEGAL or CASE"):
        publish_node(
            document_version_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            knowledge_version_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            chunks=[{"chunk_text": "x"}],
            chunk_ids=["cccccccc-cccc-cccc-cccc-cccccccccccc"],
            chunk_type="TENDER",
            actor_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        )


def test_publish_node_rejects_empty_chunk_ids():
    """publish_node 要求至少 1 个 chunk_id。"""
    from app.services.knowledge_pipeline.pipeline import publish_node

    with pytest.raises(ValueError, match="chunk_ids must be non-empty"):
        publish_node(
            document_version_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            knowledge_version_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            chunks=[],
            chunk_ids=[],
            chunk_type="LEGAL",
            actor_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        )


# -------------------------------------------------------------------
# ingest_knowledge_document（整条链路）
# -------------------------------------------------------------------


def test_ingest_knowledge_document_full_pipeline(sample_chunks):
    """整条链路：parse → embed → publish，返回 KnowledgeIngestResult。"""
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchone.return_value = ("法规内容",)

    with patch(
        "app.services.knowledge_pipeline.pipeline.parse_document_node"
    ) as mock_parse, patch(
        "app.services.knowledge_pipeline.pipeline.embed_node"
    ) as mock_embed, patch(
        "app.services.knowledge_pipeline.pipeline.publish_node"
    ) as mock_publish, patch(
        "app.services.knowledge_pipeline.pipeline.get_session_factory"
    ) as mock_factory, patch(
        "app.services.knowledge_pipeline.pipeline.clear_previous_index"
    ):
        mock_factory.return_value = MagicMock(return_value=mock_session)

        mock_parse.return_value = sample_chunks
        mock_embed.return_value = [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ]
        mock_publish.return_value = (
            "33333333-3333-3333-3333-333333333333",
            "44444444-4444-4444-4444-444444444444",
        )

        result = ingest_knowledge_document(
            file_path="/tmp/test.pdf",
            mime_type="application/pdf",
            document_version_id="55555555-5555-5555-5555-555555555555",
            knowledge_version_id="66666666-6666-6666-6666-666666666666",
            chunk_type="LEGAL",
            actor_id="77777777-7777-7777-7777-777777777777",
        )

    assert isinstance(result, KnowledgeIngestResult)
    assert result.status == "ready"
    assert result.chunk_count == 2
    assert str(result.knowledge_entry_id) == "33333333-3333-3333-3333-333333333333"
    assert str(result.knowledge_version_id) == "44444444-4444-4444-4444-444444444444"
    mock_parse.assert_called_once()
    mock_embed.assert_called_once()
    mock_publish.assert_called_once()


def test_ingest_knowledge_document_parse_failure_raises():
    """parse 失败应抛 RuntimeError，不写半完成状态。"""
    with patch(
        "app.services.knowledge_pipeline.pipeline.parse_document_node"
    ) as mock_parse, patch(
        "app.services.knowledge_pipeline.pipeline.embed_node"
    ) as mock_embed, patch(
        "app.services.knowledge_pipeline.pipeline.publish_node"
    ) as mock_publish:
        mock_parse.return_value = []  # parse 失败

        with pytest.raises(RuntimeError, match="parse failed"):
            ingest_knowledge_document(
                file_path="/nonexistent.pdf",
                mime_type="application/pdf",
                document_version_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                knowledge_version_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                chunk_type="LEGAL",
                actor_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            )

    # embed 和 publish 不应被调用（避免半完成状态）
    mock_embed.assert_not_called()
    mock_publish.assert_not_called()


def test_ingest_knowledge_document_passes_correct_chunk_type():
    """chunk_type 应贯穿整个流水线（parse → embed → publish）。"""
    with patch(
        "app.services.knowledge_pipeline.pipeline.parse_document_node"
    ) as mock_parse, patch(
        "app.services.knowledge_pipeline.pipeline.embed_node"
    ) as mock_embed, patch(
        "app.services.knowledge_pipeline.pipeline.publish_node"
    ) as mock_publish, patch(
        "app.services.knowledge_pipeline.pipeline.get_session_factory"
    ) as mock_factory, patch(
        "app.services.knowledge_pipeline.pipeline.clear_previous_index"
    ):
        mock_factory.return_value = MagicMock()
        mock_parse.return_value = [{"chunk_text": "case content"}]
        mock_embed.return_value = ["11111111-1111-1111-1111-111111111111"]
        mock_publish.return_value = (
            "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333",
        )

        ingest_knowledge_document(
            file_path="/tmp/case.pdf",
            mime_type="application/pdf",
            document_version_id="44444444-4444-4444-4444-444444444444",
            knowledge_version_id="55555555-5555-5555-5555-555555555555",
            chunk_type="CASE",
            actor_id="66666666-6666-6666-6666-666666666666",
        )

    assert mock_embed.call_args.kwargs["chunk_type"] == "CASE"
    assert mock_publish.call_args.kwargs["chunk_type"] == "CASE"
