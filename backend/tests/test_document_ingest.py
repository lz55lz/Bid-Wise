"""document_ingest 共享包测试

覆盖：
  - parser.raw_text_to_chunks：纯文本切分章节/段落
  - downloader.download_document：本地 Path / 不存在的路径 / minio URL 错误处理
  - graph.py shim：_parse_document 仍可调用（向后兼容）
"""
import os
import tempfile
from unittest.mock import MagicMock, patch

from app.services.document_ingest import (
    cleanup_temp_file,
    download_document,
    raw_text_to_chunks,
)
from app.services.document_ingest.semantic_boundaries import (
    semantic_chunk_layout_nodes,
    split_explicit_clause_boundaries,
)
from app.services.document_text_quality import indexability_gate

# -------------------------------------------------------------------
# raw_text_to_chunks
# -------------------------------------------------------------------


def test_raw_text_to_chunks_basic_paragraphs():
    """无标题的纯文本应切为单个 paragraph chunk。"""
    text = "第一行内容。\n第二行内容。\n第三行内容。"
    raw, chunks = raw_text_to_chunks(text, doc_id="doc-1")
    assert raw == text
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "paragraph"
    assert "第一行内容" in chunks[0]["chunk_text"]


def test_raw_text_to_chunks_with_markdown_headings():
    """Markdown 头（# / ##）应切为 section chunk。"""
    text = (
        "# First Chapter Title for Section One Content Area\n"
        "paragraph alpha line\n"
        "paragraph alpha second line\n"
        "## Second Chapter Title for Section Two Content Area\n"
        "paragraph beta line\n"
        "paragraph beta second line\n"
    )
    raw, chunks = raw_text_to_chunks(text, doc_id="doc-2")
    sections = [c for c in chunks if c["chunk_type"] == "section"]
    paragraphs = [c for c in chunks if c["chunk_type"] == "paragraph"]
    assert len(sections) >= 2  # 一级 + 二级
    assert len(paragraphs) >= 1


def test_raw_text_to_chunks_empty_input():
    """空文本应返回 ([], text) 无 chunk。"""
    raw, chunks = raw_text_to_chunks("", doc_id="doc-3")
    assert raw == ""
    assert chunks == []


def test_raw_text_to_chunks_chunk_id_format():
    """chunk_id 格式应为 "{doc_id}_{chunk_index}"。"""
    text = "line1\nline2\nline3"
    _, chunks = raw_text_to_chunks(text, doc_id="xyz")
    for c in chunks:
        assert c["chunk_id"].startswith("xyz_")
        assert c["doc_id"] == "xyz"


def test_raw_text_to_chunks_keeps_oversized_fallback_block_for_quality_gate():
    """Fallback 原文不能按长度截断；统一质量闸门决定是否放行。"""
    text = "投标人必须按招标文件要求提供有效资质证明材料。" * 100

    _, chunks = raw_text_to_chunks(text, doc_id="bounded")

    assert [chunk["chunk_text"] for chunk in chunks] == [text]
    assert indexability_gate(chunks[0]["chunk_text"]) == "OVERSIZED_CHUNK"


def test_raw_text_to_chunks_marks_unbreakable_oversized_fallback_block():
    """没有可恢复边界时同样保留原文，并交给清洗质量闸门拒绝。"""
    _, chunks = raw_text_to_chunks("甲" * 1300, doc_id="unresolved")

    assert len(chunks) == 1
    assert chunks[0]["chunk_text"] == "甲" * 1300
    assert indexability_gate(chunks[0]["chunk_text"]) == "OVERSIZED_CHUNK"


def test_split_explicit_clause_boundaries_splits_flattened_payment_terms():
    """同页扁平化的编号/阶段付款条款必须成为独立的原子节点。"""
    parts = split_explicit_clause_boundaries(
        "54 4.1.1 第一次支付：收到申请后 28 日内支付。"
        "第二次支付：验收合格后支付至 95%。"
        "第三次支付：缺陷责任期届满后支付剩余款项。"
        "4.2 其他：另行约定。"
    )

    assert parts == [
        "4.1.1 第一次支付：收到申请后 28 日内支付。",
        "第二次支付：验收合格后支付至 95%。",
        "第三次支付：缺陷责任期届满后支付剩余款项。",
        "4.2 其他：另行约定。",
    ]


def test_split_explicit_clause_boundaries_splits_explicit_list_items() -> None:
    parts = split_explicit_clause_boundaries(
        "投标文件应包含下列材料：（1）营业执照。（2）资质证书。（3）安全生产许可证。"
    )

    assert parts == [
        "投标文件应包含下列材料：",
        "（1）营业执照。",
        "（2）资质证书。",
        "（3）安全生产许可证。",
    ]


def test_split_explicit_clause_boundaries_keeps_cross_references_intact() -> None:
    content = "3.5.6 投标人应按本章第3.5.1项至第3.5.5项规定提供证明材料。"

    assert split_explicit_clause_boundaries(content) == [content]


def test_semantic_chunking_merges_same_page_tail_and_filters_page_number() -> None:
    chunks = semantic_chunk_layout_nodes([
        {"doc_id": "d", "chunk_index": 39, "page_no": 8, "chunk_type": "paragraph", "chunk_text": "4"},
        {"doc_id": "d", "chunk_index": 40, "page_no": 8, "chunk_type": "section", "chunk_text": "（1）投标人正受到责令停业的行政处罚或正处于财务被接管、冻结、破产的状态"},
        {"doc_id": "d", "chunk_index": 41, "page_no": 8, "chunk_type": "paragraph", "chunk_text": "内；"},
    ])

    assert len(chunks) == 1
    assert chunks[0]["chunk_text"].endswith("状态 内；")
    assert chunks[0]["parser_metadata"]["semantic_merge"] == "SAME_PAGE_SHORT_RESIDUE"


def test_semantic_chunking_keeps_numbered_items_separate() -> None:
    chunks = semantic_chunk_layout_nodes([
        {"doc_id": "d", "chunk_index": 1, "page_no": 1, "chunk_type": "paragraph", "chunk_text": "3.1 投标人必须具备资质。（1）营业执照。（2）安全生产许可证。"},
    ])

    assert [chunk["chunk_text"] for chunk in chunks] == [
        "3.1 投标人必须具备资质。",
        "（1）营业执照。",
        "（2）安全生产许可证。",
    ]


def test_semantic_chunking_drops_short_fragment_exposed_by_clause_split() -> None:
    chunks = semantic_chunk_layout_nodes([
        {"doc_id": "d", "chunk_index": 1, "page_no": 1, "chunk_type": "paragraph", "chunk_text": "注：（1）投标人应提交完整说明材料。"},
    ])

    assert [chunk["chunk_text"] for chunk in chunks] == ["（1）投标人应提交完整说明材料。"]


# -------------------------------------------------------------------
# download_document
# -------------------------------------------------------------------


def test_download_document_local_path_priority():
    """raw_text_path 存在时应优先使用，不读 doc_url。"""
    # 临时文件作为 raw_text_path
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(b"local content")
    tmp.close()
    try:
        result = download_document(raw_text_path=tmp.name, doc_url=None)
        assert result == tmp.name
    finally:
        os.unlink(tmp.name)


def test_download_document_local_path_via_doc_url():
    """raw_text_path 不存在但 doc_url 是本地 Path 时应使用 doc_url。"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(b"url content")
    tmp.close()
    try:
        result = download_document(raw_text_path=None, doc_url=tmp.name)
        assert result == tmp.name
    finally:
        os.unlink(tmp.name)


def test_download_document_none_when_all_missing():
    """所有路径都不存在时返回 None。"""
    result = download_document(
        raw_text_path="/nonexistent/path/a.pdf",
        doc_url="/nonexistent/path/b.pdf",
    )
    assert result is None


def test_download_document_minio_failure_returns_none():
    """MinIO 下载失败时返回 None。"""
    settings = MagicMock()
    settings.minio_endpoint = "http://minio:9000"
    settings.minio_access_key.get_secret_value.return_value = "ak"
    settings.minio_secret_key.get_secret_value.return_value = "sk"

    # 修复：函数内 from minio import Minio，需要 patch sys.modules 中的 minio 或真实模块
    fake_minio = MagicMock()
    fake_client = MagicMock()
    fake_client.fget_object.side_effect = Exception("connection refused")
    fake_minio.Minio.return_value = fake_client

    with patch.dict("sys.modules", {"minio": fake_minio}):
        result = download_document(
            raw_text_path=None,
            doc_url="minio://bucket/obj.pdf",
            settings=settings,
        )
    assert result is None


def test_download_document_keeps_source_suffix_for_mineru_upload() -> None:
    """MinerU uses the upload name to select PDF parsing; never downgrade it to .bin."""
    settings = MagicMock()
    settings.minio_endpoint = "http://minio:9000"
    settings.minio_access_key.get_secret_value.return_value = "ak"
    settings.minio_secret_key.get_secret_value.return_value = "sk"
    fake_minio = MagicMock()
    fake_client = MagicMock()
    fake_minio.Minio.return_value = fake_client

    with patch.dict("sys.modules", {"minio": fake_minio}):
        result = download_document(
            raw_text_path=None,
            doc_url="minio://bucket/source",
            file_name="招标文件.pdf",
            settings=settings,
        )
    try:
        assert result is not None
        assert result.endswith(".pdf")
        fake_client.fget_object.assert_called_once_with("bucket", "source", result)
    finally:
        if result:
            os.unlink(result)


def test_cleanup_temp_file_safe():
    """cleanup_temp_file 对不存在的 temp 文件应静默吞错。"""
    # 不存在的 temp 路径不应抛错
    cleanup_temp_file(tempfile.gettempdir() + "/nonexistent_xyz.pdf")
    cleanup_temp_file(None)
    cleanup_temp_file("")


# -------------------------------------------------------------------
# graph.py 兼容性 shim
# -------------------------------------------------------------------


def test_graph_parse_document_shim_returns_tuple():
    """graph._parse_document 应仍返回 (chunks, raw_text)，通过 document_ingest 实现。"""
    from app.services.bid_pipeline.graph import _parse_document

    # 即使文件不存在，shim 也应正常返回 ([], "") 不抛错
    chunks, raw_text = _parse_document("/nonexistent/file.pdf", "application/pdf", "doc-99")
    assert isinstance(chunks, list)
    assert isinstance(raw_text, str)
    assert chunks == []
    assert raw_text == ""


def test_semantic_chunking_removes_contents_entries_but_keeps_real_first_chapter() -> None:
    chunks = semantic_chunk_layout_nodes(
        [
            {"chunk_index": 0, "page_no": 1, "chunk_type": "section", "chunk_text": "目录"},
            {"chunk_index": 1, "page_no": 1, "chunk_type": "section", "chunk_text": "第一章 总则"},
            {
                "chunk_index": 2,
                "page_no": 1,
                "chunk_type": "section",
                "chunk_text": "第二章 政府采购当事人",
            },
            {
                "chunk_index": 3,
                "page_no": 1,
                "chunk_type": "section",
                "chunk_text": "第三章 政府采购方式",
            },
            {"chunk_index": 4, "page_no": 2, "chunk_type": "section", "chunk_text": "第一章 总则"},
            {
                "chunk_index": 5,
                "page_no": 2,
                "chunk_type": "paragraph",
                "chunk_text": "第一条 为了规范政府采购行为，制定本法。",
            },
        ]
    )

    assert [chunk["chunk_text"] for chunk in chunks] == [
        "第一章 总则",
        "第一条 为了规范政府采购行为，制定本法。",
    ]
