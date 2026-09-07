"""公共法规/案例知识的只读混合检索。"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.integrations.embedding import BgeM3EmbeddingClient, EmbeddingUnavailable
from app.integrations.reranker import RankV2Reranker, RerankerUnavailable
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.retrieval.query_rewrite import build_bm25_queries


logger = logging.getLogger(__name__)

class KnowledgeRetrievalService:
    """只检索已发布版本；Dense/BM25 负责召回，reranker 负责最终排序。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._repository = KnowledgeRepository(session)
        self._embedding = BgeM3EmbeddingClient(settings)
        self._reranker = RankV2Reranker(settings)

    async def search(self, query: str, limit: int) -> list[dict[str, object]]:
        normalized = query.strip()
        if not normalized:
            raise DomainError("VALIDATION_ERROR", "检索问题不能为空", 422)
        candidate_limit = max(20, min(60, limit * 3))

        dense_error: EmbeddingUnavailable | None = None
        vector_rows = []
        try:
            vector = (await self._embedding.embed([normalized]))[0]
            vector_rows = await self._repository.search_published(vector, candidate_limit)
        except EmbeddingUnavailable as exc:
            dense_error = exc

        bm25_best: dict[object, tuple[object, object, object, int]] = {}
        for bm25_query in build_bm25_queries(normalized):
            rows = await self._repository.search_published_bm25(bm25_query, candidate_limit)
            for rank, (entry, version, chunk, _score) in enumerate(rows, start=1):
                previous = bm25_best.get(chunk.id)
                if previous is None or rank < previous[3]:
                    bm25_best[chunk.id] = (entry, version, chunk, rank)

        fused: dict[object, tuple[object, object, object, float]] = {}
        for rank, (entry, version, chunk, _distance) in enumerate(vector_rows, start=1):
            fused[chunk.id] = (entry, version, chunk, 0.65 / (60 + rank))
        for entry, version, chunk, rank in bm25_best.values():
            previous = fused.get(chunk.id)
            fused[chunk.id] = (
                entry,
                version,
                chunk,
                0.35 / (60 + rank) + (previous[3] if previous else 0.0),
            )
        if not fused and dense_error is not None:
            raise DomainError("EMBEDDING_UNAVAILABLE", "向量服务暂不可用", 503) from dense_error

        candidates = sorted(fused.values(), key=lambda item: item[3], reverse=True)
        if candidates:
            try:
                scores = await self._reranker.rerank(
                    normalized,
                    [
                        "\n".join(
                            part
                            for part in (
                                chunk.content,
                                f"法规/知识：{version.title}",
                                f"章节：{chunk.section_path}" if chunk.section_path else "",
                            )
                            if part
                        )
                        for entry, version, chunk, _score in candidates
                    ],
                )
                candidates = [
                    item
                    for item, _score in sorted(
                        zip(candidates, scores, strict=True), key=lambda row: row[1], reverse=True
                    )
                ]
            except RerankerUnavailable:
                logger.warning(
                    "知识库 reranker 暂不可用，降级使用 RRF 排序 query=%r",
                    normalized[:160],
                )

        rows = candidates[:limit]
        return [
            {
                "chunk_id": str(chunk.id),
                "entry_id": str(entry.id),
                "version_id": str(version.id),
                "title": version.title,
                "knowledge_type": entry.knowledge_type,
                "source_reference": version.source_reference,
                "content": chunk.content,
                "section_path": chunk.section_path,
                "similarity": float(score),
            }
            for entry, version, chunk, score in rows
        ]
