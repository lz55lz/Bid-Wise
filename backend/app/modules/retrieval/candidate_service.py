"""Evidence 候选召回层：授权 + Dense/BM25 + RRF。

本服务只负责尽量不漏地生成候选池，不做 rerank、MMR 或邻居扩展；最终排序统一由
``RankedEvidenceRetrievalService`` 完成。
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.integrations.embedding import BgeM3EmbeddingClient, EmbeddingUnavailable
from app.modules.evidence.repository import EvidenceRepository
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService
from app.modules.retrieval.query_rewrite import build_bm25_queries
from app.modules.retrieval.schemas import EvidenceSearchHit, EvidenceSearchResponse

_RRF_K = 60
_DENSE_WEIGHT = 0.65
_LEXICAL_WEIGHT = 0.35


class EvidenceCandidateRetrievalService:
    """项目范围候选召回；PostgreSQL 授权边界先于任何模型调用。"""

    def __init__(self, session: AsyncSession, embedding_client: BgeM3EmbeddingClient) -> None:
        self._session = session
        self._embedding_client = embedding_client
        self._evidences = EvidenceRepository(session)

    async def search_project_evidences(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
        query: str,
        limit: int,
    ) -> EvidenceSearchResponse:
        await ProjectService(self._session).require_project_access(project_id, actor)
        normalized = query.strip()
        if not normalized:
            raise DomainError("VALIDATION_ERROR", "检索问题不能为空", 422)
        candidate_limit = max(limit, min(60, limit * 3))

        dense_error: EmbeddingUnavailable | None = None
        vector_rows = []
        try:
            # Dense 本身就是语义召回，不再把同义词字典拼进 query，避免改变用户意图。
            vector = (await self._embedding_client.embed([normalized]))[0]
            vector_rows = await self._evidences.list_search_candidates(
                project_id, vector, candidate_limit
            )
        except EmbeddingUnavailable as exc:
            dense_error = exc

        # BM25 只承担精确词法/编号/金额命中，不做同义词扩展。
        lexical_best: dict[UUID, tuple[object, int, str | None]] = {}
        lexical_queries = build_bm25_queries(normalized)
        for lexical_query in lexical_queries:
            rows = await self._evidences.list_bm25_search_candidates(
                project_id, lexical_query, candidate_limit
            )
            for rank, (evidence, _score, parent_context) in enumerate(rows, start=1):
                previous = lexical_best.get(evidence.id)
                if previous is None or rank < previous[1]:
                    lexical_best[evidence.id] = (evidence, rank, parent_context)

        fused: dict[UUID, tuple[object, float, str | None]] = {}
        for rank, (evidence, _distance, parent_context) in enumerate(vector_rows, start=1):
            fused[evidence.id] = (
                evidence,
                _DENSE_WEIGHT / (_RRF_K + rank),
                parent_context,
            )
        for evidence, rank, parent_context in lexical_best.values():
            previous = fused.get(evidence.id)
            score = _LEXICAL_WEIGHT / (_RRF_K + rank) + (previous[1] if previous else 0.0)
            fused[evidence.id] = (
                evidence,
                score,
                (previous[2] if previous else None) or parent_context,
            )

        if not fused and dense_error is not None:
            raise DomainError("EMBEDDING_UNAVAILABLE", "向量服务暂不可用", 503) from dense_error

        ranked = sorted(fused.values(), key=lambda item: item[1], reverse=True)
        items = [
            EvidenceSearchHit(
                evidence_id=evidence.id,
                quoted_text=(evidence.quoted_text or "").strip(),
                context_text=(evidence.quoted_text or "").strip(),
                locator=evidence.locator,
                similarity=float(score),
                parent_context=parent_context or None,
            )
            for evidence, score, parent_context in ranked[:candidate_limit]
            if (evidence.quoted_text or "").strip()
        ]
        return EvidenceSearchResponse(items=items)
