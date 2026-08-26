"""knowledge_pipeline — 法规/案例入库流水线（LEGAL/CASE）

与 bid_pipeline 解耦：
  - bid_pipeline（TENDER）：parse → LLM 提取/打标/风险分析 → 写 bid_doc_chunk
  - knowledge_pipeline（LEGAL/CASE）：parse → embed → publish，**0 次 LLM 调用**

直接写 SearchChunk（chunk_type='LEGAL'/'CASE'，project_id=NULL），
KnowledgeRagService._aretrieve 复用现有检索路径，无需感知数据来源。

节点链路：parse → embed → publish（直接函数链，不用 LangGraph 图）。
"""
from app.services.knowledge_pipeline.pipeline import (
    KnowledgeIngestResult,
    ingest_knowledge_document,
)

__all__ = [
    "ingest_knowledge_document",
    "KnowledgeIngestResult",
]