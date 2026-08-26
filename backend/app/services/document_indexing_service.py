import bisect
import hashlib
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import DocumentNode, SearchChunk
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.evidence_repository import EvidenceRepository
from app.db.repositories.search_repository import SearchRepository
from app.integrations.ai.embedding import EmbeddingClient, EmbeddingUnavailable
from app.integrations.ai.llm import RagLlm
from app.integrations.chunking import (
    DEFAULT_CHILD_CHUNK_SIZE,
    DEFAULT_PARENT_CHUNK_SIZE,
    split_parent_child,
)
from app.integrations.vector_store import VectorRecord, VectorStore, VectorStoreUnavailable
from app.services.ai_run_service import AiRunService
from app.services.task_service import RetryableDocumentTaskError, TaskService

logger = logging.getLogger(__name__)

# WeKnora 父子块参数：child 检索（精准命中），parent 作为 LLM 上下文（语义完整）
_INDEX_CHUNK_SIZE = DEFAULT_CHILD_CHUNK_SIZE
_PARENT_CHUNK_SIZE = DEFAULT_PARENT_CHUNK_SIZE
_CHUNKING_VERSION = 5
# 入库最小正文长度（不含标题前缀），过滤纯标题行等无检索价值的碎片
_MIN_CHUNK_CONTENT_LEN = 50

_INDEXABLE_DOCUMENT_TYPES: frozenset[str] = frozenset(
    {"TENDER", "ENTERPRISE", "LEGAL", "CASE"}
)

# 章节路径与文档标题在 metadata 中的最大长度
_MAX_SECTION_LEN = 160
_MAX_TITLE_LEN = 160


class DocumentIndexingService:
    """Worker-only indexing that treats PostgreSQL chunks/Evidence as the source of truth."""

    def __init__(
        self,
        session: Session,
        embedding_client: EmbeddingClient,
        vector_store: VectorStore,
        llm_client: RagLlm | None = None,
    ) -> None:
        self._session = session
        self._documents = DocumentRepository(session)
        self._evidences = EvidenceRepository(session)
        self._search = SearchRepository(session)
        self._embedding_client = embedding_client
        self._vector_store = vector_store
        self._llm = llm_client
        self._tasks = TaskService(session)

    # ------------------------------------------------------------------ #
    # Task lifecycle
    # ------------------------------------------------------------------ #
    def process(self, task_id: UUID, document_version_id: UUID) -> None:
        task = self._tasks.start_index(task_id, document_version_id)
        if task is None:
            self._session.rollback()
            task = self._tasks.get_for_update(task_id)
            if task is not None and task.status == "FAILED":
                self._retry_or_fail(
                    task_id, document_version_id, "INDEX_FAILED", "重新触发索引。"
                )
            return

        try:
            self.do_index(document_version_id)
            self._tasks.complete_index(
                task.id,
                document_version_id,
                0,
                requires_extraction=self._requires_extraction(document_version_id),
            )
        except EmbeddingUnavailable:
            self._retry_or_fail(
                task.id,
                document_version_id,
                "AI_SERVICE_UNAVAILABLE",
                "Embedding 服务未配置或不可用。",
            )
        except VectorStoreUnavailable:
            self._retry_or_fail(
                task.id,
                document_version_id,
                "VECTOR_STORE_UNAVAILABLE",
                "pgvector 向量索引服务不可用。",
            )
        except Exception:
            logger.exception(
                "index_document failed: task=%s version=%s",
                task.id,
                document_version_id,
            )
            self._fail(
                task.id,
                document_version_id,
                "INDEX_FAILED",
                "文档索引失败，请稍后重新执行。",
            )

    # ------------------------------------------------------------------ #
    # Core indexing logic
    # ------------------------------------------------------------------ #
    def do_index(self, document_version_id: UUID) -> list[SearchChunk]:
        """核心索引逻辑，不含 TaskService 管理。返回本次索引的 chunk 列表。"""
        chunks, document = self._load_or_create_chunks(document_version_id)

        # 父子块体系：只对 child（content_type=text）做 embedding/检索，
        # parent（parent_text）不入向量库，仅作为 LLM 上下文（WeKnora 方案）
        embeddable = [c for c in chunks if c.content_type == "text"]
        vectors = AiRunService(self._session).embed(
            None,
            [chunk.content for chunk in embeddable],
            [chunk.evidence_id for chunk in embeddable if chunk.evidence_id is not None],
            self._embedding_client,
        )
        if len(vectors) != len(embeddable):
            raise EmbeddingUnavailable("embedding result count does not match chunks")

        self._vector_store.upsert(
            self._build_vector_records(embeddable, vectors)
        )

        # FAQ 入库（仅 LEGAL/CASE）：为每个 child chunk 生成 3 个相似问，
        # 写入新 SearchChunk（content_type=faq，parent_chunk_id 指向原 chunk，
        # faq_metadata 存标准问/答案/相似问）。BM25/向量检索命中后由
        # _populate_faq_content 把 Q/A 拼接为可读内容。
        if document is not None:
            chunks = self._augment_with_faq(chunks, document)

        indexed_at = datetime.now(UTC)
        for chunk in chunks:
            chunk.indexed_at = indexed_at
        self._session.commit()
        self._soft_delete_older_version_chunks(document_version_id)
        return chunks

    def _augment_with_faq(
        self, chunks: list[SearchChunk], document: Any
    ) -> list[SearchChunk]:
        """LEGAL/CASE 文档：每个 child chunk 生成 FAQ 行，追加到 chunks 列表。

        FAQ 行 content 写作 "Q:标准问\nA:正文" 形式（命中后由 _populate_faq_content
        进一步规范化），同时调用 embedding API 让 Q/A 形式参与向量检索。
        """
        if (
            self._llm is None
            or document.document_type not in {"LEGAL", "CASE"}
        ):
            return chunks

        version_id = chunks[0].source_document_version_id if chunks else None
        if version_id is None:
            return chunks

        faq_chunks: list[SearchChunk] = []
        part_offset = max((c.chunk_index for c in chunks), default=-1) + 1
        # 按 start_at 排序收集 child，便于构造 prev/next 邻居上下文（WeKnora 风格）
        text_children = sorted(
            [c for c in chunks if c.content_type == "text"],
            key=lambda c: c.start_at or 0,
        )
        doc_title = document.logical_name or ""
        llm_success = llm_empty = llm_failed = 0
        for seq, child in enumerate(text_children):
            prev_content = text_children[seq - 1].content if seq > 0 else ""
            next_content = text_children[seq + 1].content if seq + 1 < len(text_children) else ""
            questions = self._llm.generate_faq_questions(
                child.content,
                count=3,
                prev_content=prev_content,
                next_content=next_content,
                doc_title=doc_title,
            )
            if not questions:
                llm_empty += 1
                continue
            llm_success += 1
            chunk_meta = child.metadata_ or {}
            qa_text = self._build_faq_content(questions, child.content)
            faq_chunks.append(
                SearchChunk(
                    id=uuid4(),
                    source_document_version_id=version_id,
                    source_node_id=child.source_node_id,
                    evidence_id=child.evidence_id,
                    project_id=child.project_id,
                    chunk_type=child.chunk_type,
                    content_type="faq",
                    chunk_index=part_offset + seq,
                    content=qa_text,
                    content_hash=hashlib.sha256(qa_text.encode("utf-8")).hexdigest(),
                    metadata_={
                        **chunk_meta,
                        "source_chunk_id": str(child.id),
                        "generated_by": "faq_question_gen",
                    },
                    parent_chunk_id=child.id,  # 命中 FAQ 可追溯到原 child
                    start_at=child.start_at,
                    end_at=child.end_at,
                    faq_metadata={
                        "standard_question": questions[0],
                        "similar_questions": questions,
                        "answers": [child.content],
                        "page_number": chunk_meta.get("page_number"),
                        "section_path": chunk_meta.get("section_path"),
                    },
                )
            )

        if not faq_chunks:
            logger.info(
                "FAQ 生成: version=%s 成功=%d 空=%d 失败=%d（未生成 FAQ）",
                version_id, llm_success, llm_empty, llm_failed,
            )
            return chunks

        logger.info(
            "FAQ 生成: version=%s 数量=%d（llm_success=%d empty=%d）",
            version_id, len(faq_chunks), llm_success, llm_empty,
        )
        self._search.add_chunks(faq_chunks)
        self._session.flush()
        # 让 FAQ 也参与向量检索（Q 形式比正文更易命中口语化提问）
        try:
            faq_vectors = self._embedding_client.embed(
                [f.content for f in faq_chunks]
            )
            if len(faq_vectors) == len(faq_chunks):
                self._vector_store.upsert(
                    self._build_vector_records(faq_chunks, faq_vectors)
                )
        except Exception:
            logger.exception("FAQ embedding 失败：FAQ 行未向量化，BM25 仍可命中")
        return [*chunks, *faq_chunks]

    @staticmethod
    def _build_faq_content(questions: list[str], answer: str) -> str:
        """构造 FAQ chunk 的 content：标准问 + 相似问 + 答案。"""
        lines = [f"Q: {questions[0]}"]
        for q in questions[1:]:
            lines.append(f"Q: {q}")
        lines.append(f"A: {answer}")
        return "\n".join(lines)

    def _soft_delete_older_version_chunks(self, document_version_id: UUID) -> None:
        """软删同文档旧版本的 chunk，避免多版本重复内容进入检索结果。"""
        from sqlalchemy import text

        result = self._session.execute(
            text(
                "UPDATE app.search_chunks sc SET deleted_at = now() "
                "FROM app.document_versions dv "
                "WHERE sc.source_document_version_id = dv.id "
                "AND dv.document_id = "
                "(SELECT document_id FROM app.document_versions WHERE id = :vid) "
                "AND dv.version_no < "
                "(SELECT version_no FROM app.document_versions WHERE id = :vid) "
                "AND sc.deleted_at IS NULL"
            ),
            {"vid": document_version_id},
        )
        if result.rowcount:
            logger.info(
                "软删旧版本 chunk: version=%s 数量=%d", document_version_id, result.rowcount
            )
        self._session.commit()

    @staticmethod
    def _build_vector_records(
        chunks: list[SearchChunk], vectors: list[list[float]]
    ) -> list[VectorRecord]:
        return [
            VectorRecord(
                pk=str(chunk.id),
                vector=vector,
                chunk_type=chunk.chunk_type,
                project_id=str(chunk.project_id or ""),
                document_version_id=str(chunk.source_document_version_id or ""),
                evidence_id=str(chunk.evidence_id or ""),
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

    # ------------------------------------------------------------------ #
    # Chunk loading / creation
    # ------------------------------------------------------------------ #
    def _load_or_create_chunks(
        self, document_version_id: UUID
    ) -> tuple[list[SearchChunk], Any]:
        """加载已存在的 chunk，否则将整篇文档拼接后一次性切块生成新 chunk。

        Returns:
            (chunks, document) 元组。已存在路径用 document=None 表示无需 FAQ 生成。
        """
        existing = self._search.list_chunks_for_version(document_version_id)
        if existing:
            return existing, None

        version, document, nodes, evidence_by_node = self._load_version_context(
            document_version_id
        )
        full_text, node_spans = self._concatenate_nodes(nodes)
        if not full_text.strip():
            raise ValueError("document has no searchable nodes")

        chunks = self._build_document_chunks(
            document=document,
            version_id=version.id,
            full_text=full_text,
            node_spans=node_spans,
            evidence_by_node=evidence_by_node,
        )
        if not chunks:
            raise ValueError("document has no searchable nodes")

        chunks = self._deduplicate_chunks(chunks)

        # 父块先 flush（子块 parent_chunk_id 外键依赖父块已入库）
        parents = [c for c in chunks if c.content_type == "parent_text"]
        children = [c for c in chunks if c.content_type != "parent_text"]
        self._search.add_chunks(parents)
        self._session.flush()
        self._search.add_chunks(children)
        self._session.flush()
        # 串联邻居链表（BM25 走 content 表达式 GIN 索引，无需额外刷新）
        self._search.link_neighbor_chunks(version.id)
        self._session.commit()
        return chunks, document

    def _load_version_context(
        self, document_version_id: UUID
    ) -> tuple[Any, Any, list[DocumentNode], dict[UUID, Any]]:
        """加载并校验文档版本上下文：version + document + nodes + evidences。"""
        version = self._documents.get_version(document_version_id)
        if version is None:
            raise ValueError("document version not found")

        document = self._documents.get_document(version.document_id)
        if document is None:
            raise ValueError("document scope is invalid")
        if document.document_type == "TENDER" and document.project_id is None:
            raise ValueError("tender document scope is invalid")
        if document.document_type not in _INDEXABLE_DOCUMENT_TYPES:
            raise ValueError("document type is not indexable")

        nodes = self._documents.list_nodes(document_version_id, 0, 1_000_000)
        evidence_by_node = {
            evidence.document_node_id: evidence
            for evidence in self._evidences.list_for_version(document_version_id)
            if evidence.document_node_id is not None
        }
        return version, document, nodes, evidence_by_node

    @staticmethod
    def _concatenate_nodes(
        nodes: list[DocumentNode],
    ) -> tuple[str, list[tuple[int, int, DocumentNode]]]:
        """按 order_no 将可索引节点拼接为整篇文本（WeKnora 整篇喂入方式）。

        Returns:
            (整篇文本, [(start, end, node), ...])，spans 记录每个节点在整篇
            文本中的 rune 区间，用于将 chunk 锚定回节点获取 evidence/页码。
        """
        ordered = sorted(
            (
                node
                for node in nodes
                if node.cleaning_metadata.get("indexable") and node.cleaned_content
            ),
            key=lambda node: node.order_no,
        )
        parts: list[str] = []
        spans: list[tuple[int, int, DocumentNode]] = []
        offset = 0
        for node in ordered:
            content = node.cleaned_content
            parts.append(content)
            spans.append((offset, offset + len(content), node))
            offset += len(content) + 1  # "\n" 连接符
        return "\n".join(parts), spans

    @staticmethod
    def _deduplicate_chunks(chunks: list[SearchChunk]) -> list[SearchChunk]:
        """按 content_hash 去重，避免相同内容重复 embed。"""
        seen_hashes: set[str] = set()
        unique_chunks: list[SearchChunk] = []
        for chunk in chunks:
            if chunk.content_hash in seen_hashes:
                continue
            seen_hashes.add(chunk.content_hash)
            unique_chunks.append(chunk)
        if len(unique_chunks) < len(chunks):
            logger.info("去重 chunk: %d -> %d", len(chunks), len(unique_chunks))
        return unique_chunks

    # ------------------------------------------------------------------ #
    # Chunk construction primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_chunk_content(
        raw_chunk: Any,
        section_display: str,
        doc_title_display: str,
    ) -> str:
        """
        构建 chunk 文本。
        优先级：doc_title > context_header > section_path（WeKnora EmbeddingContent 标准）。
        """
        chunk_content = raw_chunk.content.strip()
        if not chunk_content:
            return ""

        if doc_title_display:
            if raw_chunk.context_header and raw_chunk.context_header != doc_title_display:
                header_prefix = f"{doc_title_display} / {raw_chunk.context_header}"
            else:
                header_prefix = doc_title_display
            return f"{header_prefix}\n\n{chunk_content}"

        if raw_chunk.context_header:
            return f"{raw_chunk.context_header}\n\n{chunk_content}"

        if section_display:
            return f"{section_display}\n{chunk_content}"

        return chunk_content

    @classmethod
    def _build_document_chunks(
        cls,
        *,
        document: Any,
        version_id: UUID,
        full_text: str,
        node_spans: list[tuple[int, int, DocumentNode]],
        evidence_by_node: dict[UUID, Any],
    ) -> list[SearchChunk]:
        """对整篇文档做父子块切块（strategy=auto 由文档画像选择策略层）。

        parent（content_type=parent_text）：大上下文块，不参与检索，仅作 LLM 上下文。
        child（content_type=text）：小块，参与 embedding/BM25 检索，携带 parent_chunk_id。
        """
        doc_title_display = (document.logical_name or "").strip()[:_MAX_TITLE_LEN]
        default_evidence = evidence_by_node.get(node_spans[0][2].id)
        span_starts = [start for start, _, _ in node_spans]

        raw_parents, raw_children = split_parent_child(
            full_text,
            parent_size=_PARENT_CHUNK_SIZE,
            child_size=_INDEX_CHUNK_SIZE,
            strategy="auto",
        )
        if not raw_children:
            return []

        # 先建父块实体（子块需要 parent_chunk_id 引用）
        # 父块 chunk_index 用负数序号，避免与子块在 uq_search_chunks_source 上冲突
        parent_entities: list[SearchChunk] = []
        for parent_seq, raw_parent in enumerate(raw_parents):
            anchor = cls._anchor_node(node_spans, span_starts, raw_parent.start)
            parent_evidence = evidence_by_node.get(anchor.id) or default_evidence
            parent_entities.append(
                cls._make_search_chunk(
                    project_id=document.project_id,
                    chunk_type=document.document_type,
                    document_version_id=version_id,
                    anchor_node=anchor,
                    section_display=(anchor.section_path or "").strip()[:_MAX_SECTION_LEN],
                    doc_title_display=doc_title_display,
                    evidence_id=parent_evidence.id if parent_evidence else None,
                    raw_chunk=raw_parent,
                    part_index=parent_seq,
                    part_count=len(raw_parents),
                    chunk_content=raw_parent.content.strip(),
                    content_type="parent_text",
                    chunk_index=-(parent_seq + 1),
                )
            )

        total = len(raw_children)
        chunks: list[SearchChunk] = []
        for part_index, raw_chunk in enumerate(raw_children):
            # 过滤纯标题行等碎片（标题信息已通过 context_header 带给相邻块）
            if len(raw_chunk.content.strip()) < _MIN_CHUNK_CONTENT_LEN:
                continue
            anchor = cls._anchor_node(node_spans, span_starts, raw_chunk.start)
            evidence = evidence_by_node.get(anchor.id) or default_evidence
            if evidence is None:
                continue
            section_display = (anchor.section_path or "").strip()[:_MAX_SECTION_LEN]
            chunk_content = cls._build_chunk_content(
                raw_chunk, section_display, doc_title_display
            )
            if not chunk_content:
                continue
            child = cls._make_search_chunk(
                project_id=document.project_id,
                chunk_type=document.document_type,
                document_version_id=version_id,
                anchor_node=anchor,
                section_display=section_display,
                doc_title_display=doc_title_display,
                evidence_id=evidence.id,
                raw_chunk=raw_chunk,
                part_index=part_index,
                part_count=total,
                chunk_content=chunk_content,
            )
            if 0 <= raw_chunk.parent_index < len(parent_entities):
                child.parent_chunk_id = parent_entities[raw_chunk.parent_index].id
            chunks.append(child)
        # 父块排在子块之后统一返回（入库顺序不影响检索）
        return [*chunks, *parent_entities]

    @staticmethod
    def _anchor_node(
        node_spans: list[tuple[int, int, DocumentNode]],
        span_starts: list[int],
        char_start: int,
    ) -> DocumentNode:
        """按 chunk 起始偏移锚定到覆盖它的节点（spans 按 start 升序）。"""
        idx = bisect.bisect_right(span_starts, char_start) - 1
        return node_spans[max(idx, 0)][2]

    @staticmethod
    def _make_search_chunk(
        *,
        project_id: UUID | None,
        chunk_type: str,
        document_version_id: UUID,
        anchor_node: DocumentNode,
        section_display: str,
        doc_title_display: str,
        evidence_id: UUID | None,
        raw_chunk: Any,
        part_index: int,
        part_count: int,
        chunk_content: str,
        content_type: str = "text",
        chunk_index: int | None = None,
    ) -> SearchChunk:
        """构造单个 SearchChunk 实体。"""
        return SearchChunk(
            id=uuid4(),
            source_document_version_id=document_version_id,
            source_node_id=anchor_node.id,
            evidence_id=evidence_id,
            project_id=project_id,
            chunk_type=chunk_type,
            content_type=content_type,
            chunk_index=part_index if chunk_index is None else chunk_index,
            content=chunk_content,
            content_hash=hashlib.sha256(chunk_content.encode("utf-8")).hexdigest(),
            metadata_={
                "node_type": anchor_node.node_type,
                "page_number": anchor_node.page_number,
                "section_path": section_display or None,
                "cleaned": True,
                "chunking_version": _CHUNKING_VERSION,
                "part_index": part_index,
                "part_count": part_count,
                "char_start": raw_chunk.start,
                "char_end": raw_chunk.end,
                "context_header": raw_chunk.context_header or None,
                "doc_title": doc_title_display or None,
            },
            start_at=raw_chunk.start,
            end_at=raw_chunk.end,
        )

    # ------------------------------------------------------------------ #
    # Task helpers
    # ------------------------------------------------------------------ #
    def _requires_extraction(self, document_version_id: UUID) -> bool:
        version = self._documents.get_version(document_version_id)
        document = None if version is None else self._documents.get_document(version.document_id)
        if document is None:
            raise ValueError("document not found")
        return document.document_type == "TENDER"

    def _fail(self, task_id: UUID, document_version_id: UUID, code: str, message: str) -> None:
        self._session.rollback()
        self._tasks.fail_index(task_id, document_version_id, code, message)

    def _retry_or_fail(
        self, task_id: UUID, document_version_id: UUID, code: str, message: str
    ) -> None:
        self._session.rollback()
        if self._tasks.retry_index_or_fail(task_id, document_version_id, code, message):
            raise RetryableDocumentTaskError(code, message)
