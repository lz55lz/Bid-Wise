"""document_ingest.parser — PDF/文档三级 fallback 解析

从 bid_pipeline/graph.py 抽出。调用方负责把解析节点落到自己的持久层。

MinerU 节点在这里原样保留；面向 Requirement 的条款派生由
``TenderClauseService`` 完成，避免解析层和业务层重复切块。

三级 fallback：
  1. HttpMinerU — 远程高精度解析（首选）
  2. LocalPdfParser (pypdfium2) — 本地解析（次选）
  3. MarkItdownClient — 通用 fallback（兜底）
"""
import logging
from pathlib import Path
from typing import Any

from app.services.document_ingest.semantic_boundaries import (
    semantic_chunk_layout_nodes,
    split_explicit_clause_boundaries,
)

logger = logging.getLogger(__name__)


def parse_document(
    file_path: str | Path, mime_type: str, doc_id: int | str
) -> tuple[list[dict[str, Any]], str]:
    """三级 fallback 解析：HttpMinerU → LocalPdfParser → MarkItdownClient。

    Args:
        file_path: 本地文件路径
        mime_type: MIME 类型（"application/pdf" 等）
        doc_id: 文档标识（用于生成 chunk_id 前缀；不绑定具体业务表）

    Returns:
        (chunks, raw_text)
        chunks: list[dict]，每个含
            chunk_id/doc_id/chunk_index/page_no/section_path/chunk_text/chunk_type
        raw_text: 拼接后的全文
        全部 parser 都失败时返回 ([], "")
    """
    file_path = Path(file_path)
    settings = _get_settings()

    # tier 1: HttpMinerU
    try:
        from app.integrations.mineru import HttpMinerUClient, ParserUnavailable

        mineru = HttpMinerUClient(settings)
        result = mineru.parse(file_path, mime_type)
        if result.nodes:
            chunks: list[dict[str, Any]] = []
            for i, node in enumerate(result.nodes):
                if node.content.strip():
                    chunks.append(
                        {
                            "chunk_id": f"{doc_id}_{i}",
                            "doc_id": doc_id,
                            "chunk_index": i,
                            "page_no": node.page_number,
                            "section_path": node.section_path or "",
                            "chunk_text": node.content.strip(),
                            "chunk_type": node.node_type.lower(),
                            "bbox": node.bbox,
                            "parser_metadata": node.metadata,
                        }
                    )
            # MinerU 提供版面事实；这里将同页短残句回接到所属条款，并且只在
            # 文本中明确出现的法律/招标编号处分开。不会按字符窗口截断。
            chunks = semantic_chunk_layout_nodes(chunks)
            raw_text = "\n".join(c["chunk_text"] for c in chunks)
            logger.info(f"[document_ingest] MinerU parsed {len(chunks)} chunks")
            return chunks, raw_text
    except ParserUnavailable as e:
        logger.warning(f"[document_ingest] MinerU unavailable: {e}")

    # tier 2: LocalPdfParser (pypdfium2)
    raw_text = ""
    try:
        from app.integrations.pdf_parser import LocalPdfParser

        parser = LocalPdfParser()
        result = parser.parse(file_path)
        raw_text = result.get("text", "") or ""
        page_chunks: list[dict[str, Any]] = []
        for page in result.get("pages", []):
            page_text = str(page.get("text", ""))
            if not page_text.strip():
                continue
            _, chunks = raw_text_to_chunks(page_text, doc_id)
            for chunk in chunks:
                chunk["page_no"] = page["page_number"]
                page_chunks.append(chunk)
        for index, chunk in enumerate(page_chunks):
            chunk["chunk_index"] = index
            chunk["chunk_id"] = f"{doc_id}_{index}"
        if page_chunks:
            logger.info(
                "[document_ingest] LocalPdfParser parsed %d page-aware chunks",
                len(page_chunks),
            )
            return page_chunks, raw_text
    except Exception as e:
        logger.warning(f"[document_ingest] LocalPdfParser failed: {e}")

    if raw_text:
        raw_text_out, chunks = raw_text_to_chunks(raw_text, doc_id)
        if chunks:
            logger.info(f"[document_ingest] LocalPdfParser parsed {len(chunks)} chunks")
            return chunks, raw_text_out

    # tier 3: MarkitdownClient (通用 fallback)
    try:
        from app.integrations.markitdown_parser import MarkItdownClient

        md = MarkItdownClient()
        result = md.parse(file_path, mime_type)
        raw_text = result.raw_output.decode("utf-8") if result.raw_output else ""
    except Exception as e:
        logger.warning(f"[document_ingest] MarkItdownClient failed: {e}")

    if raw_text:
        raw_text_out, chunks = raw_text_to_chunks(raw_text, doc_id)
        if chunks:
            logger.info(f"[document_ingest] MarkItdown parsed {len(chunks)} chunks")
            return chunks, raw_text_out

    return [], ""


def raw_text_to_chunks(
    text: str, doc_id: int | str
) -> tuple[str, list[dict[str, Any]]]:
    """将原始文本按章节/段落拆分为 chunks。

    标题识别：`#` Markdown 头 + MarkItdownClient 的 heading 检测。

    此入口只给没有版面结构的本地解析器使用。它只恢复可验证的编号条款，
    不以字符数量合并、拆分或丢弃正文；异常大块由统一质量闸门拒绝进入下游。
    """
    from app.integrations.markitdown_parser import MarkItdownClient

    nodes: list[dict[str, Any]] = []
    section_path: list[str] = []
    para_buffer: list[str] = []
    chunk_index = 0

    def flush_paragraph() -> None:
        nonlocal chunk_index
        if not para_buffer:
            return
        content = "\n".join(para_buffer)
        sec = " / ".join(section_path) if section_path else ""
        nodes.append(
            {
                "chunk_id": f"{doc_id}_{chunk_index}",
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "page_no": None,
                "section_path": sec,
                "chunk_text": content,
                "chunk_type": "paragraph",
            }
        )
        chunk_index += 1
        para_buffer.clear()

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Markdown 头（# 开头）
        if line.startswith("#"):
            flush_paragraph()
            level = len(line) - len(line.lstrip("#"))
            title = line.lstrip("#").strip()
            if not title:
                continue
            if level == 1:
                section_path = [title]
            else:
                section_path.append(title)
            nodes.append(
                {
                    "chunk_id": f"{doc_id}_{chunk_index}",
                    "doc_id": doc_id,
                    "chunk_index": chunk_index,
                    "page_no": None,
                    "section_path": " / ".join(section_path[-3:]),
                    "chunk_text": title,
                    "chunk_type": "section",
                }
            )
            chunk_index += 1
        # MarkItdown heading 检测
        elif MarkItdownClient._is_likely_heading(line):
            flush_paragraph()
            section_path.append(line)
            nodes.append(
                {
                    "chunk_id": f"{doc_id}_{chunk_index}",
                    "doc_id": doc_id,
                    "chunk_index": chunk_index,
                    "page_no": None,
                    "section_path": " / ".join(section_path[-3:]),
                    "chunk_text": line,
                    "chunk_type": "section",
                }
            )
            chunk_index += 1
        else:
            para_buffer.append(line)

    flush_paragraph()
    # 扁平文本只能恢复文档中明确写出的编号/阶段边界；不再以长度合并短段落。
    atomic: list[dict[str, Any]] = []
    for node in nodes:
        parts = split_explicit_clause_boundaries(node["chunk_text"])
        if len(parts) <= 1:
            atomic.append(node)
            continue
        for part in parts:
            atomic.append({**node, "chunk_text": part, "chunk_type": "clause"})
    for chunk_index, chunk in enumerate(atomic):
        chunk["chunk_index"] = chunk_index
        chunk["chunk_id"] = f"{doc_id}_{chunk_index}"
    return text, atomic


def _get_settings():
    """延迟导入 settings（避免循环依赖）。"""
    from app.core.config import get_settings

    return get_settings()
