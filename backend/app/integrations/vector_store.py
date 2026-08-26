from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings

# WeKnora RRF 融合参数
_RRF_K = 60  # RRF 常数，防止除零
_VECTOR_WEIGHT = 0.7
_KEYWORD_WEIGHT = 0.3

# 草稿不得占用 ANN/BM25 候选名额；否则再晚一步的授权回查会把所有候选过滤空。
_PUBLISHED_KNOWLEDGE_EXISTS = """
    EXISTS (
        SELECT 1
        FROM app.knowledge_versions kv
        JOIN app.knowledge_entries ke ON ke.id = kv.knowledge_entry_id
        WHERE kv.source_document_version_id = {chunk_ref}.source_document_version_id
          AND kv.status = 'PUBLISHED'
          AND ke.deleted_at IS NULL
    )
"""


class VectorStoreUnavailable(Exception):
    """Vector store cannot perform the requested operation."""


@dataclass(frozen=True, slots=True)
class VectorRecord:
    pk: str
    vector: list[float]
    chunk_type: str
    project_id: str
    document_version_id: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    pk: str
    score: float | None = None  # cosine similarity (1 = perfect match, higher is better)


class VectorStore(Protocol):
    def upsert(self, records: Sequence[VectorRecord]) -> None: ...

    def delete(self, pks: Sequence[str]) -> None: ...

    def search(self, vector: list[float], project_id: str, limit: int) -> list[VectorSearchHit]: ...

    def search_tender_version(
        self, vector: list[float], project_id: str, document_version_id: str, limit: int
    ) -> list[VectorSearchHit]: ...

    def search_enterprise(self, vector: list[float], limit: int) -> list[VectorSearchHit]: ...

    def search_knowledge(self, vector: list[float], limit: int) -> list[VectorSearchHit]: ...

    def search_hybrid_knowledge(
        self,
        vector: list[float],
        query: str,
        limit: int,
        vector_weight: float = _VECTOR_WEIGHT,
    ) -> list[VectorSearchHit]: ...

    def search_hybrid_tender_version(
        self,
        vector: list[float],
        query: str,
        project_id: str,
        document_version_id: str,
        limit: int,
        vector_weight: float = _VECTOR_WEIGHT,
    ) -> list[VectorSearchHit]: ...


class PgVectorStore:
    """PostgreSQL + pgvector implementation of VectorStore.

    Uses pgvector HNSW index for approximate nearest neighbor search with cosine similarity.
    Embedding vectors are stored directly in the SearchChunk.embedding column.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @staticmethod
    def _new_session() -> Session:
        """每次操作创建独立 session，调用方负责关闭（避免长驻连接泄漏）。"""
        from app.db.session import get_session_factory

        return get_session_factory()()

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        from app.db.models import SearchChunk

        session = self._new_session()
        try:
            chunk_ids = [r.pk for r in records]
            existing = {
                str(c.id): c
                for c in session.query(SearchChunk).filter(SearchChunk.id.in_(chunk_ids)).all()
            }
            for record in records:
                chunk = existing.get(record.pk)
                if chunk:
                    chunk.embedding = record.vector
            session.commit()
        finally:
            session.close()

    def delete(self, pks: Sequence[str]) -> None:
        """Remove vector embeddings from chunks (BM25 records preserved for retrieval)."""
        from app.db.models import SearchChunk

        if not pks:
            return
        session = self._new_session()
        try:
            session.query(SearchChunk).filter(SearchChunk.id.in_(pks)).update(
                {"embedding": None}, synchronize_session=False
            )
            session.commit()
        finally:
            session.close()

    def search(self, vector: list[float], project_id: str, limit: int) -> list[VectorSearchHit]:
        from app.db.models import SearchChunk
        return self._search_by_sql(
            vector,
            SearchChunk.id,
            SearchChunk.embedding,
            text(
                "chunk_type = 'TENDER' AND project_id = :project_id "
                "AND deleted_at IS NULL AND embedding IS NOT NULL"
            ),
            {"project_id": project_id},
            limit,
        )

    def search_tender_version(
        self, vector: list[float], project_id: str, document_version_id: str, limit: int
    ) -> list[VectorSearchHit]:
        from app.db.models import SearchChunk
        return self._search_by_sql(
            vector,
            SearchChunk.id,
            SearchChunk.embedding,
            text(
                "chunk_type = 'TENDER' AND project_id = :project_id "
                "AND source_document_version_id = :doc_version_id "
                "AND deleted_at IS NULL AND embedding IS NOT NULL"
            ),
            {"project_id": project_id, "doc_version_id": document_version_id},
            limit,
        )

    def search_knowledge(self, vector: list[float], limit: int) -> list[VectorSearchHit]:
        from app.db.models import SearchChunk
        published = _PUBLISHED_KNOWLEDGE_EXISTS.format(chunk_ref="app.search_chunks")
        return self._search_by_sql(
            vector,
            SearchChunk.id,
            SearchChunk.embedding,
            text(
                "chunk_type IN ('LEGAL', 'CASE') AND project_id IS NULL "
                f"AND deleted_at IS NULL AND embedding IS NOT NULL AND {published}"
            ),
            {},
            limit,
        )

    def search_bm25_knowledge(
        self,
        query: str,
        limit: int,
    ) -> list[VectorSearchHit]:
        """BM25 full-text search for legal/case documents."""
        if not query or limit <= 0:
            return []
        session = self._new_session()
        try:
            published = _PUBLISHED_KNOWLEDGE_EXISTS.format(chunk_ref="sc")
            sql = text(f"""
                SELECT sc.id::text AS chunk_id,
                       ts_rank(
                           to_tsvector('zh', sc.content),
                           plainto_tsquery('zh', :query)
                       ) AS bm25_score
                FROM app.search_chunks sc
                WHERE sc.chunk_type IN ('LEGAL', 'CASE')
                  AND sc.project_id IS NULL
                  AND sc.deleted_at IS NULL
                  AND sc.content_type != 'parent_text'
                  AND {published}
                  AND to_tsvector('zh', sc.content) @@ plainto_tsquery('zh', :query)
                ORDER BY bm25_score DESC, sc.id
                LIMIT :limit
            """)
            rows = session.execute(sql, {"query": query, "limit": limit}).fetchall()
            return [
                VectorSearchHit(
                    pk=str(row.chunk_id),
                    score=float(row.bm25_score) if row.bm25_score else 0.0,
                )
                for row in rows
            ]
        except Exception as exc:
            raise VectorStoreUnavailable(f"BM25 knowledge search failed: {exc}") from exc
        finally:
            session.close()

    def search_hybrid_knowledge(
        self,
        vector: list[float],
        query: str,
        limit: int,
        vector_weight: float = _VECTOR_WEIGHT,
    ) -> list[VectorSearchHit]:
        """Hybrid retrieval for legal/case: RRF fusion of vector + BM25."""
        if not vector or not query or limit <= 0:
            return []
        session = self._new_session()
        try:
            # Vector ANN search
            vec_str = "[" + ",".join(str(v) for v in vector) + "]"
            published = _PUBLISHED_KNOWLEDGE_EXISTS.format(chunk_ref="sc")
            vector_sql = text(f"""
                SELECT sc.id::text AS chunk_id,
                       1.0 - (sc.embedding::vector <=> CAST('{vec_str}' AS vector)) AS vector_score
                FROM app.search_chunks sc
                WHERE sc.chunk_type IN ('LEGAL', 'CASE')
                  AND sc.project_id IS NULL
                  AND sc.deleted_at IS NULL
                  AND sc.embedding IS NOT NULL
                  AND {published}
                ORDER BY vector_score DESC
                LIMIT :limit
            """)
            vector_rows = session.execute(vector_sql, {"limit": limit}).fetchall()

            # BM25 search
            bm25_sql = text(f"""
                SELECT sc.id::text AS chunk_id,
                       ts_rank(
                           to_tsvector('zh', sc.content),
                           plainto_tsquery('zh', :query)
                       ) AS bm25_score
                FROM app.search_chunks sc
                WHERE sc.chunk_type IN ('LEGAL', 'CASE')
                  AND sc.project_id IS NULL
                  AND sc.deleted_at IS NULL
                  AND sc.content_type != 'parent_text'
                  AND {published}
                  AND to_tsvector('zh', sc.content) @@ plainto_tsquery('zh', :query)
                ORDER BY bm25_score DESC, sc.id
                LIMIT :limit
            """)
            bm25_rows = session.execute(bm25_sql, {"query": query, "limit": limit}).fetchall()

            # RRF fusion
            keyword_weight = 1.0 - vector_weight
            rrf_scores: dict[str, float] = {}
            for rank, row in enumerate(vector_rows, start=1):
                pk = str(row.chunk_id)
                rrf_scores[pk] = rrf_scores.get(pk, 0.0) + vector_weight / (_RRF_K + rank)

            for rank, row in enumerate(bm25_rows, start=1):
                pk = str(row.chunk_id)
                rrf_scores[pk] = rrf_scores.get(pk, 0.0) + keyword_weight / (_RRF_K + rank)

            sorted_pks = sorted(rrf_scores, key=lambda pk: rrf_scores[pk], reverse=True)
            return [
                VectorSearchHit(pk=pk, score=rrf_scores[pk])
                for pk in sorted_pks[:limit]
            ]
        except Exception as exc:
            raise VectorStoreUnavailable(f"hybrid knowledge search failed: {exc}") from exc
        finally:
            session.close()

    def search_enterprise(self, vector: list[float], limit: int) -> list[VectorSearchHit]:
        from app.db.models import SearchChunk
        return self._search_by_sql(
            vector,
            SearchChunk.id,
            SearchChunk.embedding,
            text(
                "chunk_type = 'ENTERPRISE' AND project_id IS NULL "
                "AND deleted_at IS NULL AND embedding IS NOT NULL"
            ),
            {},
            limit,
        )

    def _search_by_sql(
        self,
        vector: list[float],
        id_col,
        embedding_col,
        where_clause,
        params: dict,
        limit: int,
    ) -> list[VectorSearchHit]:
        if not vector or limit <= 0:
            return []
        session = self._new_session()
        try:
            # pgvector 需要方括号格式 [...]，且必须用字符串拼接传递（不能用参数绑定）
            vector_literal = "[" + ",".join(str(v) for v in vector) + "]"
            # 用 literal_binds=False 保留参数化，只拼接向量
            where_sql = str(where_clause.compile(compile_kwargs={'literal_binds': False}))
            sql = text(f"""
                SELECT id::text AS chunk_id,
                       1.0 - (embedding::vector <=> CAST('{vector_literal}' AS vector)) AS score
                FROM app.search_chunks
                WHERE {where_sql}
                  AND embedding IS NOT NULL
                ORDER BY score DESC
                LIMIT :limit
            """)
            rows = session.execute(sql, {"limit": limit, **params}).fetchall()
            return [
                VectorSearchHit(pk=str(row.chunk_id), score=float(row.score) if row.score else 0.0)
                for row in rows
            ]
        except Exception as exc:
            raise VectorStoreUnavailable(f"pgvector search failed: {exc}") from exc
        finally:
            session.close()

    def search_bm25_tender_version(
        self,
        query: str,
        project_id: str,
        document_version_id: str,
        limit: int,
    ) -> list[VectorSearchHit]:
        """BM25 full-text search for tender documents using PostgreSQL tsvector."""
        if not query or limit <= 0:
            return []
        session = self._new_session()
        try:
            # 用 tsvector @@ plainto_tsquery 做中文全文检索，ts_rank 评分
            sql = text("""
                SELECT id::text AS chunk_id,
                       ts_rank(
                           to_tsvector('zh', content),
                           plainto_tsquery('zh', :query)
                       ) AS score
                FROM app.search_chunks
                WHERE chunk_type = 'TENDER'
                  AND project_id = :project_id
                  AND source_document_version_id = :doc_version_id
                  AND deleted_at IS NULL
                  AND content_type != 'parent_text'
                  AND to_tsvector('zh', content) @@ plainto_tsquery('zh', :query)
                ORDER BY score DESC, id
                LIMIT :limit
            """)
            rows = session.execute(
                sql,
                {
                    "query": query,
                    "project_id": project_id,
                    "doc_version_id": document_version_id,
                    "limit": limit,
                },
            ).fetchall()
            return [
                VectorSearchHit(pk=str(row.chunk_id), score=float(row.score) if row.score else 0.0)
                for row in rows
            ]
        except Exception as exc:
            raise VectorStoreUnavailable(f"BM25 search failed: {exc}") from exc
        finally:
            session.close()

    def search_hybrid_tender_version(
        self,
        vector: list[float],
        query: str,
        project_id: str,
        document_version_id: str,
        limit: int,
        vector_weight: float = _VECTOR_WEIGHT,
    ) -> list[VectorSearchHit]:
        """Hybrid retrieval: RRF fusion of vector ANN search + BM25 full-text search.

        WeKnora pattern: RRF(k=60, vector_weight=0.7, keyword_weight=0.3)
        """
        if not vector or not query or limit <= 0:
            return []
        session = self._new_session()
        try:
            # Step 1: Vector ANN search
            vec_str = "[" + ",".join(str(v) for v in vector) + "]"
            vector_sql = text(f"""
                SELECT id::text AS chunk_id,
                       1.0 - (embedding::vector <=> CAST('{vec_str}' AS vector)) AS vector_score
                FROM app.search_chunks
                WHERE chunk_type = 'TENDER'
                  AND project_id = :project_id
                  AND source_document_version_id = :doc_version_id
                  AND deleted_at IS NULL
                  AND embedding IS NOT NULL
                ORDER BY vector_score DESC
                LIMIT :limit
            """)
            vector_rows = session.execute(
                vector_sql,
                {"project_id": project_id, "doc_version_id": document_version_id, "limit": limit},
            ).fetchall()

            # Step 2: BM25 search
            bm25_sql = text("""
                SELECT id::text AS chunk_id,
                       ts_rank(
                           to_tsvector('zh', content),
                           plainto_tsquery('zh', :query)
                       ) AS bm25_score
                FROM app.search_chunks
                WHERE chunk_type = 'TENDER'
                  AND project_id = :project_id
                  AND source_document_version_id = :doc_version_id
                  AND deleted_at IS NULL
                  AND content_type != 'parent_text'
                  AND to_tsvector('zh', content) @@ plainto_tsquery('zh', :query)
                ORDER BY bm25_score DESC, id
                LIMIT :limit
            """)
            bm25_rows = session.execute(
                bm25_sql,
                {
                    "query": query,
                    "project_id": project_id,
                    "doc_version_id": document_version_id,
                    "limit": limit,
                },
            ).fetchall()

            # Step 3: RRF fusion
            keyword_weight = 1.0 - vector_weight
            rrf_scores: dict[str, float] = {}
            for rank, row in enumerate(vector_rows, start=1):
                pk = str(row.chunk_id)
                rrf_scores[pk] = rrf_scores.get(pk, 0.0) + vector_weight / (_RRF_K + rank)

            for rank, row in enumerate(bm25_rows, start=1):
                pk = str(row.chunk_id)
                rrf_scores[pk] = rrf_scores.get(pk, 0.0) + keyword_weight / (_RRF_K + rank)

            # Sort by RRF score descending
            sorted_pks = sorted(rrf_scores, key=lambda pk: rrf_scores[pk], reverse=True)
            return [
                VectorSearchHit(pk=pk, score=rrf_scores[pk])
                for pk in sorted_pks[:limit]
            ]
        except Exception as exc:
            raise VectorStoreUnavailable(f"hybrid search failed: {exc}") from exc
        finally:
            session.close()

    def check_available(self) -> None:
        session = self._new_session()
        try:
            session.execute(text("SELECT 1"))
        except Exception as exc:
            raise VectorStoreUnavailable(f"pgvector unavailable: {exc}") from exc
        finally:
            session.close()

    def validate_embedding_dimension(self, dimension: int) -> None:
        EMBEDDING_DIM = 1024
        if dimension != EMBEDDING_DIM:
            raise VectorStoreUnavailable(
                f"embedding dimension must be {EMBEDDING_DIM}, got {dimension}"
            )
