from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.constants import RAG_RETRIEVAL_LIMIT, RERANK_CANDIDATE_LIMIT
from app.db.repositories.evidence_repository import EvidenceRepository
from app.db.repositories.knowledge_repository import KnowledgeRepository
from app.db.repositories.search_repository import KnowledgeSearchRecord, SearchRepository
from app.integrations.ai.embedding import EmbeddingClient, EmbeddingUnavailable
from app.integrations.ai.reranker import RankerClient, RankerUnavailable
from app.integrations.vector_store import VectorStore, VectorStoreUnavailable
from app.services.query_rewrite_service import rewrite_query

_CONTEXT_LIMIT = 10
_QUERY_CHARS = 6_000
_CONTEXT_CHARS = 1_600  # 法律文本需要更多上下文
_MMR_OVERLAP_THRESHOLD = 0.85


class LegalKnowledgeRetrievalUnavailable(RuntimeError):
    """The fixed retrieval pipeline cannot safely produce legal context."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    evidence_id: UUID
    title: str
    source_reference: str
    knowledge_version_id: UUID
    content: str
    score: float = 0.0  # 向量分数，用于 reranker 失败降级


class LegalKnowledgeRetriever:
    """Retrieve only PostgreSQL-authorized, published LEGAL/CASE evidence for an Agent run."""

    def __init__(
        self,
        session: Session,
        embedding_client: EmbeddingClient,
        vector_store: VectorStore,
        reranker: RankerClient,
    ) -> None:
        self._search = SearchRepository(session)
        self._knowledge = KnowledgeRepository(session)
        self._evidences = EvidenceRepository(session)
        self._embedding_client = embedding_client
        self._vector_store = vector_store
        self._reranker = reranker

    def retrieve(
        self, tender_evidence: list[dict[str, object]]
    ) -> tuple[list[dict[str, object]], set[UUID]]:
        query = self._query(tender_evidence)
        if not query:
            return [], set()

        # Query rewrite for better retrieval
        rewrite_result = rewrite_query(query)
        embed_query = rewrite_result.expanded_query
        rerank_query = rewrite_result.rerank_query

        try:
            vectors = self._embedding_client.embed([embed_query])
            if len(vectors) != 1:
                raise EmbeddingUnavailable("legal query embedding count is invalid")
            # RRF 融合搜索：向量 + BM25 并行召回，RRF 融合排名
            hits = self._vector_store.search_hybrid_knowledge(
                vectors[0], embed_query, RAG_RETRIEVAL_LIMIT
            )
        except (EmbeddingUnavailable, VectorStoreUnavailable) as exc:
            raise LegalKnowledgeRetrievalUnavailable(
                "legal knowledge retrieval is unavailable"
            ) from exc

        # RRF 分数排序，取前 RERANK_CANDIDATE_LIMIT 个传入 reranker
        # 传入 hit 分数用于 reranker 失败降级
        candidates = self._vector_candidates(
            [(hit.pk, hit.score or 0.0) for hit in hits[:RERANK_CANDIDATE_LIMIT]]
        )
        candidates.extend(
            self._manual_candidates({candidate.evidence_id for candidate in candidates})
        )
        if not candidates:
            return [], set()
        try:
            scores = self._reranker.rerank(
                rerank_query, [candidate.content for candidate in candidates]
            )
            if len(scores) != len(candidates):
                raise RankerUnavailable("legal reranker result count is invalid")
            ranked = [
                candidate
                for _, candidate in sorted(
                    zip(scores, candidates, strict=True), key=lambda item: item[0], reverse=True
                )
            ]
        except RankerUnavailable:
            # 降级：按向量分数排序
            ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
        # MMR 多样性去冗
        ranked = self._mmr_deduplicate(ranked)
        ranked = ranked[:_CONTEXT_LIMIT]
        return (
            [
                {
                    "evidence_id": str(candidate.evidence_id),
                    "title": candidate.title,
                    "source_reference": candidate.source_reference,
                    "knowledge_version_id": str(candidate.knowledge_version_id),
                    "content": candidate.content[:_CONTEXT_CHARS],
                }
                for candidate in ranked
            ],
            {candidate.evidence_id for candidate in ranked},
        )

    def _vector_candidates(self, chunk_pks_and_scores: list[tuple[str, float]]) -> list[_Candidate]:
        chunk_pks = [pk for pk, _ in chunk_pks_and_scores]
        by_pk = {
            str(record.chunk.id): record
            for record in self._search.list_visible_knowledge_chunks(chunk_pks)
        }
        score_by_pk = {pk: score for pk, score in chunk_pks_and_scores}
        return [
            self._candidate_from_record(by_pk[pk], score_by_pk.get(pk, 0.0))
            for pk, _ in chunk_pks_and_scores
            if pk in by_pk
        ]

    def _manual_candidates(self, existing_evidence_ids: set[UUID]) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for entry, version, evidence_id in self._knowledge.list_published_manual_knowledge(
            limit=RAG_RETRIEVAL_LIMIT
        ):
            if evidence_id in existing_evidence_ids or self._evidences.get(evidence_id) is None:
                continue
            content = version.content.strip()
            if content:
                candidates.append(
                    _Candidate(
                        evidence_id=evidence_id,
                        title=entry.title,
                        source_reference=entry.source_reference,
                        knowledge_version_id=version.id,
                        content=content[:_QUERY_CHARS],
                    )
                )
        return candidates

    def _candidate_from_record(
        self, record: KnowledgeSearchRecord, score: float = 0.0
    ) -> _Candidate:
        return _Candidate(
            evidence_id=record.evidence.id,
            title=record.entry.title,
            source_reference=record.entry.source_reference,
            knowledge_version_id=record.knowledge_version.id,
            content=self._search.expand_chunk_context(record.chunk),
            score=score,
        )

    @classmethod
    def _mmr_deduplicate(cls, candidates: list[_Candidate]) -> list[_Candidate]:
        """MMR 多样性去冗：按分数顺序选入，丢弃与已选内容字符重叠率超过阈值的候选项。"""
        selected: list[_Candidate] = []
        selected_contents: list[set[str]] = []
        for candidate in candidates:
            tokens = set(candidate.content.split())
            if not tokens:
                continue
            is_duplicate = False
            for sel_tokens in selected_contents:
                overlap = len(tokens & sel_tokens)
                union = len(tokens | sel_tokens)
                if union > 0 and overlap / union >= _MMR_OVERLAP_THRESHOLD:
                    is_duplicate = True
                    break
            if not is_duplicate:
                selected.append(candidate)
                selected_contents.append(tokens)
        return selected

    @staticmethod
    def _query(tender_evidence: list[dict[str, object]]) -> str:
        parts = [
            str(item.get("content", "")).strip()
            for item in tender_evidence
            if str(item.get("content", "")).strip()
        ]
        return "\n".join(parts)[:_QUERY_CHARS]
