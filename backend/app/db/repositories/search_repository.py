from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Text, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    AiRun,
    Document,
    DocumentVersion,
    EnterpriseMaterial,
    Evidence,
    KnowledgeEntry,
    KnowledgeVersion,
    MatchResult,
    MaterialDocument,
    SearchChunk,
)


@dataclass(frozen=True, slots=True)
class KnowledgeSearchRecord:
    chunk: SearchChunk
    entry: KnowledgeEntry
    knowledge_version: KnowledgeVersion
    document: Document
    document_version: DocumentVersion
    evidence: Evidence


@dataclass(frozen=True, slots=True)
class Bm25Hit:
    """BM25 search result with score."""
    chunk: SearchChunk
    rank: int
    bm25_score: float


class SearchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # BM25 / Full-Text Search using PostgreSQL ts_rank
    # RRF_FUSION_K is the constant for Reciprocal Rank Fusion (k=60 is standard)
    RRF_K = 60

    def get_chunk(self, chunk_id: UUID) -> SearchChunk | None:
        """按 ID 获取未删除的 chunk（父子块解析等场景）。"""
        statement = select(SearchChunk).where(
            SearchChunk.id == chunk_id,
            SearchChunk.deleted_at.is_(None),
        )
        return self._session.scalars(statement).first()

    def expand_chunk_context(
        self, chunk: SearchChunk, *, preceding: int = 1, following: int = 6, max_chars: int = 6_000
    ) -> str:
        """Expand a retrieved knowledge chunk through its same-document neighbour chain."""
        before: list[str] = []
        cursor = chunk
        for _ in range(preceding):
            if cursor.pre_chunk_id is None:
                break
            previous = self.get_chunk(cursor.pre_chunk_id)
            if (
                previous is None
                or previous.source_document_version_id != chunk.source_document_version_id
            ):
                break
            before.append(previous.content)
            cursor = previous

        parts = list(reversed(before)) + [chunk.content]
        cursor = chunk
        for _ in range(following):
            if cursor.next_chunk_id is None:
                break
            following_chunk = self.get_chunk(cursor.next_chunk_id)
            if (
                following_chunk is None
                or following_chunk.source_document_version_id != chunk.source_document_version_id
            ):
                break
            parts.append(following_chunk.content)
            cursor = following_chunk
            if sum(len(part) for part in parts) >= max_chars:
                break
        return "\n".join(parts)[:max_chars]

    def get_chunks_content_by_pks(self, pks: list[str]) -> dict[str, str]:
        """批量按 PK 取 chunk content（仅 id + content 两列，轻量）。

        用于 RRF 阶段为 vector hits 补 content，以便 keyword boost 覆盖向量召回路径。
        不做可见性过滤（content 是只读数据，不含敏感字段）。
        """
        if not pks:
            return {}
        statement = select(SearchChunk.id, SearchChunk.content).where(
            SearchChunk.id.in_(pks),
            SearchChunk.deleted_at.is_(None),
        )
        return {
            str(row[0]): row[1] or ""
            for row in self._session.execute(statement).tuples()
        }

    def get_chunk_scopes_by_pks(self, pks: list[str]) -> dict[str, tuple[str | None, UUID | None]]:
        """批量按 PK 取 (chunk_type, project_id)，供来源权重使用。

        RRF 融合只有 pk + 分数，向量命中不携带来源；此查询让调用方
        在融合阶段就能区分 project / enterprise / knowledge 来源。
        """
        if not pks:
            return {}
        statement = select(
            SearchChunk.id, SearchChunk.chunk_type, SearchChunk.project_id
        ).where(
            SearchChunk.id.in_(pks),
            SearchChunk.deleted_at.is_(None),
        )
        return {
            str(row[0]): (row[1], row[2])
            for row in self._session.execute(statement).tuples()
        }

    def search_chunks_bm25(
        self,
        query: str,
        chunk_type: str | None = None,
        project_id: UUID | None = None,
        top_k: int = 20,
    ) -> list[Bm25Hit]:
        """Full-text search using PostgreSQL ts_rank, returns top-k results."""
        if not query or not query.strip():
            return []
        # Use 'zh' config with zhparser for Chinese text segmentation
        tsquery = func.plainto_tsquery("zh", query)
        rank_expr = func.ts_rank(
            func.to_tsvector("zh", cast(SearchChunk.content, Text)), tsquery
        ).label("rank")

        statement = (
            select(SearchChunk, rank_expr)
            .where(
                SearchChunk.deleted_at.is_(None),
                SearchChunk.content != "",
                # 父块（parent_text）不参与检索，仅作为命中子块的 LLM 上下文
                SearchChunk.content_type != "parent_text",
            )
            .order_by(rank_expr.desc())
            .limit(top_k)
        )
        if chunk_type:
            statement = statement.where(SearchChunk.chunk_type == chunk_type)
        if project_id is not None:
            statement = statement.where(SearchChunk.project_id == project_id)
        else:
            statement = statement.where(SearchChunk.project_id.is_(None))

        results: list[Bm25Hit] = []
        for row in self._session.execute(statement).tuples():
            chunk: SearchChunk = row[0]
            bm25_score: float = float(row[1])
            results.append(Bm25Hit(chunk=chunk, rank=len(results), bm25_score=bm25_score))
        return results

    def add_run(self, run: AiRun) -> None:
        self._session.add(run)

    def list_chunks_for_version(self, document_version_id: UUID) -> list[SearchChunk]:
        statement = (
            select(SearchChunk)
            .where(
                SearchChunk.source_document_version_id == document_version_id,
                SearchChunk.deleted_at.is_(None),
            )
            .order_by(SearchChunk.chunk_index, SearchChunk.id)
        )
        return list(self._session.scalars(statement))

    def add_chunks(self, chunks: list[SearchChunk]) -> None:
        self._session.add_all(chunks)

    def link_neighbor_chunks(self, document_version_id: UUID) -> None:
        """为同一文档版本的 child chunk 串联 pre_chunk_id / next_chunk_id 链表。

        只链接 content_type='text' 的子块（父块不参与检索，无需邻居链）。
        调用时机：chunks 已 flush 到 DB（id 已生成）之后。
        按 chunk_index 排序后逐个设置前向/后向链接。
        """
        chunks = [
            c
            for c in self.list_chunks_for_version(document_version_id)
            if c.content_type == "text"
        ]
        if len(chunks) < 2:
            return
        for prev, curr in zip(chunks[:-1], chunks[1:], strict=False):
            curr.pre_chunk_id = prev.id
            prev.next_chunk_id = curr.id
        # 头尾清空
        chunks[0].pre_chunk_id = None
        chunks[-1].next_chunk_id = None

    def get_neighbor_chunks(
        self, chunk_id: UUID, direction: str = "both"
    ) -> tuple[SearchChunk | None, SearchChunk | None]:
        """获取邻居 chunk。

        Args:
            chunk_id: 当前 chunk ID
            direction: "both" | "prev" | "next"

        Returns:
            (prev_chunk, next_chunk)
        """
        chunk = self._session.get(SearchChunk, chunk_id)
        if chunk is None:
            return None, None
        prev_id = chunk.pre_chunk_id
        next_id = chunk.next_chunk_id
        prev_chunk = self._session.get(SearchChunk, prev_id) if prev_id else None
        next_chunk = self._session.get(SearchChunk, next_id) if next_id else None
        if direction == "prev":
            return prev_chunk, None
        if direction == "next":
            return None, next_chunk
        return prev_chunk, next_chunk

    def list_visible_project_chunks(
        self,
        project_id: UUID,
        chunk_ids: list[str],
        *,
        document_version_id: UUID | None = None,
    ) -> list[SearchChunk]:
        if not chunk_ids:
            return []
        statement = (
            select(SearchChunk)
            .join(Evidence, Evidence.id == SearchChunk.evidence_id)
            .join(DocumentVersion, DocumentVersion.id == SearchChunk.source_document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                SearchChunk.project_id == project_id,
                SearchChunk.chunk_type == "TENDER",
                SearchChunk.id.in_(chunk_ids),
                SearchChunk.indexed_at.is_not(None),
                SearchChunk.deleted_at.is_(None),
                Evidence.document_version_id == SearchChunk.source_document_version_id,
                Evidence.document_node_id == SearchChunk.source_node_id,
                Document.project_id == project_id,
                Document.deleted_at.is_(None),
            )
        )
        if document_version_id is not None:
            statement = statement.where(
                SearchChunk.source_document_version_id == document_version_id
            )
        return list(self._session.scalars(statement))

    def list_visible_enterprise_chunks(
        self, project_id: UUID, chunk_ids: list[str]
    ) -> list[SearchChunk]:
        if not chunk_ids:
            return []
        statement = (
            select(SearchChunk)
            .join(DocumentVersion, DocumentVersion.id == SearchChunk.source_document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(
                Evidence,
                and_(
                    Evidence.id == SearchChunk.evidence_id,
                    Evidence.document_version_id == SearchChunk.source_document_version_id,
                    Evidence.document_node_id == SearchChunk.source_node_id,
                ),
            )
            .join(MaterialDocument, MaterialDocument.document_version_id == DocumentVersion.id)
            .join(EnterpriseMaterial, EnterpriseMaterial.id == MaterialDocument.material_id)
            .join(
                MatchResult,
                and_(
                    MatchResult.material_id == EnterpriseMaterial.id,
                    MatchResult.project_id == project_id,
                    MatchResult.is_current.is_(True),
                ),
            )
            .where(
                SearchChunk.project_id.is_(None),
                SearchChunk.chunk_type == "ENTERPRISE",
                SearchChunk.id.in_(chunk_ids),
                SearchChunk.indexed_at.is_not(None),
                SearchChunk.deleted_at.is_(None),
                Document.document_type == "ENTERPRISE",
                Document.deleted_at.is_(None),
                EnterpriseMaterial.deleted_at.is_(None),
                EnterpriseMaterial.status == "CONFIRMED",
            )
        )
        return list(self._session.scalars(statement))

    def list_visible_knowledge_chunks(self, chunk_ids: list[str]) -> list[KnowledgeSearchRecord]:
        if not chunk_ids:
            return []
        # knowledge_pipeline 写入的 chunk 没有节点粒度（source_node_id 与
        # Evidence.document_node_id 均为 NULL），NULL = NULL 不成立会整行丢失，
        # 因此该 join 必须容忍双 NULL。
        node_match = or_(
            Evidence.document_node_id == SearchChunk.source_node_id,
            and_(
                Evidence.document_node_id.is_(None),
                SearchChunk.source_node_id.is_(None),
            ),
        )
        statement = (
            select(
                SearchChunk,
                KnowledgeEntry,
                KnowledgeVersion,
                Document,
                DocumentVersion,
                Evidence,
            )
            .join(DocumentVersion, DocumentVersion.id == SearchChunk.source_document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(
                Evidence,
                and_(
                    Evidence.id == SearchChunk.evidence_id,
                    Evidence.document_version_id == SearchChunk.source_document_version_id,
                    node_match,
                ),
            )
            .join(
                KnowledgeVersion,
                KnowledgeVersion.source_document_version_id
                == SearchChunk.source_document_version_id,
            )
            .join(KnowledgeEntry, KnowledgeEntry.id == KnowledgeVersion.knowledge_entry_id)
            .where(
                SearchChunk.project_id.is_(None),
                SearchChunk.chunk_type.in_(("LEGAL", "CASE")),
                SearchChunk.id.in_(chunk_ids),
                SearchChunk.indexed_at.is_not(None),
                SearchChunk.deleted_at.is_(None),
                KnowledgeVersion.status == "PUBLISHED",
                KnowledgeEntry.deleted_at.is_(None),
                Document.document_type.in_(("LEGAL", "CASE")),
                Document.deleted_at.is_(None),
            )
        )
        return [KnowledgeSearchRecord(*row) for row in self._session.execute(statement).tuples()]
