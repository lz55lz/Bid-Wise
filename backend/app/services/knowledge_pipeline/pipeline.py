"""knowledge_pipeline 主流程：parse → embed → publish

整条链路 0 次 LLM 调用，所有处理本地完成：
  1. parse：复用 document_ingest.parser 解析 PDF
  2. embed：调 EmbeddingClient 算 BGE-M3 向量
  3. publish：写 SearchChunk + Evidence + KnowledgeEntry + KnowledgeVersion(PUBLISHED)

调用方负责：
  - 下载文件（用 document_ingest.downloader）
  - 创建 DocumentVersion 记录（API 层）
  - 错误重试（ARQ worker 层）
"""
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.db.models import DocumentNode, Evidence, KnowledgeVersion
from app.db.session import get_session_factory
from app.integrations.ai.embedding import EmbeddingUnavailable
from app.services.document_ingest import parse_document

logger = logging.getLogger(__name__)

# BGE-M3 支持 8k token；500 字截断会让向量只代表段首，与 BM25/重排看到的全文错位
_EMBED_INPUT_MAX_CHARS = 2000


@dataclass(frozen=True, slots=True)
class KnowledgeIngestResult:
    """knowledge_pipeline 执行的最终结果。"""

    knowledge_entry_id: UUID
    knowledge_version_id: UUID
    document_version_id: UUID
    chunk_count: int
    status: str  # "ready" | "failed"


# -------------------------------------------------------------------
# 节点 1: parse_document_node — 复用 document_ingest
# -------------------------------------------------------------------


def parse_document_node(
    *,
    file_path: str,
    mime_type: str,
    document_version_id: UUID,
) -> list[dict[str, Any]]:
    """调用 document_ingest 解析 PDF，返回 chunks。

    返回 chunks 不写入 DB（由 embed_node 统一持久化，避免半完成状态）。
    """
    logger.info(
        f"[knowledge_pipeline] parse start: version_id={document_version_id}"
    )
    chunks, raw_text = parse_document(file_path, mime_type, str(document_version_id))
    if not chunks:
        logger.error(
            f"[knowledge_pipeline] parse failed: version_id={document_version_id}"
        )
        return []
    logger.info(
        f"[knowledge_pipeline] parse done: version_id={document_version_id}, chunks={len(chunks)}"
    )
    return chunks


# -------------------------------------------------------------------
# 节点 2: embed_node — 向量化并写入 SearchChunk + Evidence
# -------------------------------------------------------------------


def embed_node(
    *,
    chunks: list[dict[str, Any]],
    document_version_id: UUID,
    chunk_type: str,  # "LEGAL" or "CASE"
) -> list[UUID]:
    """对 chunks 调 EmbeddingClient，写入 SearchChunk（含向量和邻居链）。

    Returns:
        新建的 SearchChunk.id 列表（供 publish_node 关联 Evidence）。
    """
    if not chunks:
        return []

    if chunk_type not in ("LEGAL", "CASE"):
        raise ValueError(f"chunk_type must be LEGAL or CASE, got {chunk_type!r}")

    texts = [c["chunk_text"][:_EMBED_INPUT_MAX_CHARS] for c in chunks]
    session = get_session_factory()()

    try:
        # Embedding（同步，外部 API）
        from app.core.config import get_settings
        from app.integrations.ai.embedding import BgeM3Client

        client = BgeM3Client(get_settings())
        try:
            vectors = client.embed(texts)
        except EmbeddingUnavailable as exc:
            logger.error(f"[knowledge_pipeline] embedding failed: {exc}")
            raise

        chunk_ids: list[UUID] = []
        for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            content = chunk["chunk_text"][:10000]
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            vec_str = "[" + ",".join(map(str, vector)) + "]"

            search_chunk_id = uuid.uuid4()
            chunk_ids.append(search_chunk_id)

            session.execute(
                text("""
                    INSERT INTO app.search_chunks
                    (id, source_document_version_id, source_node_id, evidence_id,
                     project_id, chunk_type, chunk_index, content, content_hash,
                     embedding, metadata, indexed_at, deleted_at,
                     pre_chunk_id, next_chunk_id, content_type)
                    VALUES
                    (:id, :doc_version_id, NULL, NULL,
                     NULL, :chunk_type, :chunk_index, :content, :content_hash,
                     CAST(:vec AS vector(1024)), CAST(:metadata AS jsonb), NOW(), NULL,
                     NULL, NULL, 'text')
                """),
                {
                    "id": str(search_chunk_id),
                    "doc_version_id": str(document_version_id),
                    "chunk_type": chunk_type,
                    "chunk_index": i,
                    "content": content,
                    "content_hash": content_hash,
                    "vec": vec_str,
                    "metadata": json.dumps({
                        "page_no": chunk.get("page_no"),
                        "section_path": chunk.get("section_path") or "",
                        "parser_chunk_type": chunk.get("chunk_type") or "paragraph",
                    }),
                },
            )

        # 串联邻居块链表（pre/next）：一次 UPDATE 用 VALUES 关联更新全部行
        if len(chunk_ids) > 1:
            id_strs = [str(cid) for cid in chunk_ids]
            values = ", ".join(
                f"(:id{i}, :next{i}, :pre{i})" for i in range(len(id_strs))
            )
            params: dict[str, str] = {}
            for i, cid in enumerate(id_strs):
                params[f"id{i}"] = cid
                params[f"next{i}"] = id_strs[i + 1] if i + 1 < len(id_strs) else None  # type: ignore[assignment]
                params[f"pre{i}"] = id_strs[i - 1] if i > 0 else None  # type: ignore[assignment]
            session.execute(
                text(f"""
                    UPDATE app.search_chunks AS sc
                    SET next_chunk_id = CAST(v.next_id AS uuid),
                        pre_chunk_id = CAST(v.pre_id AS uuid)
                    FROM (VALUES {values}) AS v(id, next_id, pre_id)
                    WHERE sc.id::text = v.id
                """),
                params,
            )

        session.commit()
        logger.info(
            f"[knowledge_pipeline] embed done: chunks={len(chunk_ids)} chunk_type={chunk_type}"
        )
        return chunk_ids

    finally:
        session.close()


# -------------------------------------------------------------------
# 节点 3: publish_node — 写 KnowledgeEntry + KnowledgeVersion + Evidence
# -------------------------------------------------------------------


def publish_node(
    *,
    document_version_id: UUID,
    knowledge_version_id: UUID,
    chunks: list[dict[str, Any]],
    chunk_ids: list[UUID],
    chunk_type: str,  # "LEGAL" or "CASE"
    actor_id: UUID,
) -> tuple[UUID, UUID]:
    """将解析结果写回上传时创建的草稿知识版本，并关联 Evidence。

    每个 chunk 对应一条 Evidence（page_number 取解析器的真实页码，
    quoted_text 取 chunk_text 前 200 字符）。

    Returns:
        (knowledge_entry_id, knowledge_version_id)
    """
    if chunk_type not in ("LEGAL", "CASE"):
        raise ValueError(f"chunk_type must be LEGAL or CASE, got {chunk_type!r}")
    if not chunk_ids:
        raise ValueError("chunk_ids must be non-empty")

    session = get_session_factory()()
    try:
        version = session.get(KnowledgeVersion, knowledge_version_id)
        if version is None or version.source_document_version_id != document_version_id:
            raise ValueError("knowledge version does not belong to document version")
        if version.status != "DRAFT":
            raise ValueError("only a draft knowledge version can be indexed")

        # 每个 chunk 一条 Evidence，并与已写入的 SearchChunk 关联。
        # chunk 内容已在内存中，不需要逐条回查 DB
        first_evidence_id: UUID | None = None
        for order_no, (chunk_id, chunk) in enumerate(
            zip(chunk_ids, chunks, strict=True)
        ):
            quoted_text = chunk["chunk_text"][:1_000]
            evidence_id = uuid.uuid4()
            node_id = uuid.uuid4()
            if first_evidence_id is None:
                first_evidence_id = evidence_id
            session.add(
                DocumentNode(
                    id=node_id,
                    document_version_id=document_version_id,
                    parent_node_id=None,
                    node_type=_document_node_type(chunk.get("chunk_type")),
                    page_number=chunk.get("page_no"),
                    section_path=chunk.get("section_path") or None,
                    tender_req_candidate=False,
                    order_no=order_no,
                    content=chunk["chunk_text"],
                    content_hash=hashlib.sha256(chunk["chunk_text"].encode()).hexdigest(),
                    cleaned_content=None,
                    cleaning_metadata={},
                    bbox=chunk.get("bbox"),
                    metadata_=chunk.get("parser_metadata") or {},
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                Evidence(
                    id=evidence_id,
                    source_type="DOCUMENT_TEXT",
                    document_node_id=node_id,
                    document_version_id=document_version_id,
                    page_number=chunk.get("page_no"),
                    quoted_text=quoted_text,
                    content_hash=hashlib.sha256(quoted_text.encode()).hexdigest(),
                    bbox=chunk.get("bbox"),
                    source_reference={
                        "knowledge_version_id": str(version.id),
                        "source_document_version_id": str(document_version_id),
                        "page_no": chunk.get("page_no"),
                        "section_path": chunk.get("section_path") or "",
                    },
                    confidence=1.0,
                    created_at=datetime.now(UTC),
                    created_by=actor_id,
                )
            )
            # 下方是 Core text UPDATE，SQLAlchemy 不会为它自动 flush ORM 新对象；
            # 先落节点与 Evidence，才能满足两项外键。
            session.flush()
            session.execute(
                text(
                    "UPDATE app.search_chunks SET evidence_id = :eid, "
                    "source_node_id = :nid WHERE id = :cid"
                ),
                {"eid": str(evidence_id), "nid": str(node_id), "cid": str(chunk_id)},
            )

        if first_evidence_id is not None:
            session.execute(
                text(
                "UPDATE app.knowledge_versions SET source_evidence_id = :eid, content = :content "
                    "WHERE id = :vid"
                ),
                {
                    "eid": str(first_evidence_id),
                    "vid": str(version.id),
                    "content": "\n\n".join(chunk["chunk_text"] for chunk in chunks)[:100_000],
                },
            )

        session.commit()
        logger.info(
            f"[knowledge_pipeline] index done: version={version.id} chunks={len(chunk_ids)}"
        )
        return version.knowledge_entry_id, version.id

    finally:
        session.close()


def _document_node_type(value: object) -> str:
    """Map parser-specific labels onto the immutable DocumentNode enum."""
    normalized = str(value or "paragraph").upper()
    allowed = {"SECTION", "PARAGRAPH", "TABLE", "CELL", "IMAGE", "LIST"}
    return normalized if normalized in allowed else "PARAGRAPH"


# -------------------------------------------------------------------
# 主流程：ingest_knowledge_document
# -------------------------------------------------------------------


def clear_previous_index(document_version_id: UUID) -> None:
    """在重试前清理同一源文件版本的旧索引，保证召回不会混入失败尝试。"""
    session = get_session_factory()()
    try:
        session.execute(
            text(
                "UPDATE app.knowledge_versions SET source_evidence_id = NULL "
                "WHERE source_document_version_id = :id"
            ),
            {"id": str(document_version_id)},
        )
        session.execute(
            text("DELETE FROM app.search_chunks WHERE source_document_version_id = :id"),
            {"id": str(document_version_id)},
        )
        session.execute(
            text("DELETE FROM app.evidences WHERE document_version_id = :id"),
            {"id": str(document_version_id)},
        )
        session.execute(
            text("DELETE FROM app.document_nodes WHERE document_version_id = :id"),
            {"id": str(document_version_id)},
        )
        session.commit()
    finally:
        session.close()


def ingest_knowledge_document(
    *,
    file_path: str,
    mime_type: str,
    document_version_id: UUID,
    knowledge_version_id: UUID,
    chunk_type: str,  # "LEGAL" or "CASE"
    actor_id: UUID,
) -> KnowledgeIngestResult:
    """knowledge_pipeline 整条链路：parse → embed → publish。

    失败语义：任一节点失败抛异常，由 ARQ worker 重试；不写半完成状态。
    """
    chunks = parse_document_node(
        file_path=file_path,
        mime_type=mime_type,
        document_version_id=document_version_id,
    )
    if not chunks:
        raise RuntimeError(
            f"parse failed for document_version_id={document_version_id}"
        )

    clear_previous_index(document_version_id)
    chunk_ids = embed_node(
        chunks=chunks,
        document_version_id=document_version_id,
        chunk_type=chunk_type,
    )

    entry_id, version_id = publish_node(
        document_version_id=document_version_id,
        knowledge_version_id=knowledge_version_id,
        chunks=chunks,
        chunk_ids=chunk_ids,
        chunk_type=chunk_type,
        actor_id=actor_id,
    )

    return KnowledgeIngestResult(
        knowledge_entry_id=entry_id,
        knowledge_version_id=version_id,
        document_version_id=document_version_id,
        chunk_count=len(chunk_ids),
        status="ready",
    )
