"""document_ingest — 文档解析共享前置包

提供给两条 pipeline 共用：
  - bid_pipeline（TENDER）：解析后接 LLM 提取/打标/风险分析
  - knowledge_pipeline（LEGAL/CASE）：解析后直接 embed + publish

不耦合任何业务表（document_nodes / bid_doc_chunk / KnowledgeVersion）的写入，
调用方负责把 chunks 落到自己的持久层。
"""
from app.services.document_ingest.downloader import cleanup_temp_file, download_document
from app.services.document_ingest.parser import (
    parse_document,
    raw_text_to_chunks,
)
from app.services.document_ingest.semantic_boundaries import semantic_chunk_layout_nodes

__all__ = [
    "parse_document",
    "raw_text_to_chunks",
    "semantic_chunk_layout_nodes",
    "download_document",
    "cleanup_temp_file",
]
