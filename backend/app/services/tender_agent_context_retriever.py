from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.constants import RAG_RETRIEVAL_LIMIT, RERANK_CANDIDATE_LIMIT
from app.db.models import SearchChunk
from app.db.repositories.search_repository import SearchRepository
from app.integrations.ai.embedding import EmbeddingClient, EmbeddingUnavailable
from app.integrations.ai.reranker import RankerClient, RankerUnavailable
from app.integrations.vector_store import VectorSearchHit, VectorStore, VectorStoreUnavailable

_SPECIALIST_CONTEXT_LIMIT = 10
_OVERVIEW_CONTEXT_LIMIT = 12
_LEGAL_CONTEXT_LIMIT = 10
_CONTEXT_CHARS = 1_200
_CONTEXT_OVERLAP_CHARS = 160
_MAX_SEGMENTS_PER_CHUNK = 4
_MMR_OVERLAP_THRESHOLD = 0.85  # 字符重叠率超过此值认为重复
_QUERIES = {
    "overview": (
        "招标项目范围、资格条件、实质性响应、评审方法、合同条款、"
        "投标截止时间、工期、技术规范、商务要求和废标条款"
    ),
    "qualification": "投标人资格 资质证书 业绩 人员 财务 审计 联合体 准入条件 否决",
    "commercial": "报价 最高限价 支付 履约保证金 投标保证金 合同 商务偏离 税费",
    "technical": "技术规范 参数 方案 实施 服务 验收 性能 标准 响应偏离",
    "scoring": "评标办法 评分标准 分值 加分 扣分 资格审查 技术评分 商务评分",
    "schedule": "投标截止 开标 答疑 踏勘 工期 交付 计划 里程碑 有效期",
    "legal": (
        "法律合规 强制性条款 资格否决 废标 违约责任 保证金 保密 知识产权 争议解决 合同条款"
    ),
}
_SPECIALISTS = ("qualification", "commercial", "technical", "scoring", "schedule")


class TenderAgentContextRetrievalUnavailable(RuntimeError):
    """The indexed tender cannot be safely retrieved for the Agent graph."""


@dataclass(frozen=True, slots=True)
class TenderAgentContexts:
    overview: list[dict[str, object]]
    specialist_contexts: dict[str, list[dict[str, object]]]
    legal_context: list[dict[str, object]]
    evidence_ids: set[UUID]


@dataclass(frozen=True, slots=True)
class _Passage:
    chunk: SearchChunk
    content: str
    char_start: int
    char_end: int


class TenderAgentContextRetriever:
    """Retrieves bounded, version-scoped tender Evidence for fixed specialist roles."""

    def __init__(
        self,
        session: Session,
        embedding_client: EmbeddingClient,
        vector_store: VectorStore,
        reranker: RankerClient,
    ) -> None:
        self._search = SearchRepository(session)
        self._embedding_client = embedding_client
        self._vector_store = vector_store
        self._reranker = reranker

    def retrieve(self, project_id: UUID, document_version_id: UUID) -> TenderAgentContexts:
        names = tuple(_QUERIES)
        try:
            vectors = self._embedding_client.embed([_QUERIES[name] for name in names])
            if len(vectors) != len(names):
                raise EmbeddingUnavailable("tender agent query embedding count is invalid")
            # RRF 融合搜索：向量 + BM25 并行召回，RRF 融合排名
            hit_groups: list[list[VectorSearchHit]] = []
            for name, vector in zip(names, vectors, strict=True):
                hits = self._vector_store.search_hybrid_tender_version(
                    vector,
                    _QUERIES[name],
                    str(project_id),
                    str(document_version_id),
                    RAG_RETRIEVAL_LIMIT,
                )
                hit_groups.append(hits)
            all_pks = [hit.pk for hits in hit_groups for hit in hits]
            chunks_by_pk = self._authorized_chunks(project_id, document_version_id, all_pks)
            ranked_contexts = {
                name: self._rerank(
                    _QUERIES[name], self._passages_for_hits(hit_groups[index], chunks_by_pk)
                )
                for index, name in enumerate(names)
            }
        except (EmbeddingUnavailable, VectorStoreUnavailable, RankerUnavailable) as exc:
            raise TenderAgentContextRetrievalUnavailable(
                "tender agent context retrieval is unavailable"
            ) from exc

        overview = ranked_contexts["overview"][:_OVERVIEW_CONTEXT_LIMIT]
        if not overview:
            overview = self._fallback_overview(ranked_contexts)
        specialist_contexts = {
            name: (ranked_contexts[name][:_SPECIALIST_CONTEXT_LIMIT] or overview)
            for name in _SPECIALISTS
        }
        legal_context = ranked_contexts["legal"][:_LEGAL_CONTEXT_LIMIT] or overview
        evidence_ids = {
            UUID(str(item["evidence_id"]))
            for contexts in [overview, legal_context, *specialist_contexts.values()]
            for item in contexts
        }
        return TenderAgentContexts(
            overview=overview,
            specialist_contexts=specialist_contexts,
            legal_context=legal_context,
            evidence_ids=evidence_ids,
        )

    def _authorized_chunks(
        self,
        project_id: UUID,
        document_version_id: UUID,
        chunk_pks: list[str],
    ) -> dict[str, SearchChunk]:
        unique_pks = list(dict.fromkeys(chunk_pks))
        return {
            str(chunk.id): chunk
            for chunk in self._search.list_visible_project_chunks(
                project_id,
                unique_pks,
                document_version_id=document_version_id,
            )
        }

    def _passages_for_hits(
        self, hits: list[VectorSearchHit], chunks_by_pk: dict[str, SearchChunk]
    ) -> list[_Passage]:
        # RRF 分数排序：分数越高越相关，取前 RERANK_CANDIDATE_LIMIT 个
        filtered = sorted(hits, key=lambda h: h.score or 0.0, reverse=True)
        passages = [
            passage
            for hit in filtered
            if hit.pk in chunks_by_pk
            for passage in self._passages(chunks_by_pk[hit.pk])
        ][:RERANK_CANDIDATE_LIMIT]
        return passages

    def _rerank(self, query: str, passages: list[_Passage]) -> list[dict[str, object]]:
        if not passages:
            return []
        # 尝试 rerank，失败则降级到向量分数排序
        try:
            scores = self._reranker.rerank(query, [passage.content for passage in passages])
            if len(scores) != len(passages):
                raise RankerUnavailable("tender agent reranker result count is invalid")
            ranked = [
                passage
                for _, passage in sorted(
                    zip(scores, passages, strict=True), key=lambda item: item[0], reverse=True
                )
            ]
        except RankerUnavailable:
            # 降级：按向量分数（cosine distance，升序=越相似）排序
            ranked = sorted(
                passages,
                key=lambda p: (p.chunk.score or 0.0, p.chunk.metadata_.get("score", 0.0)),
            )
        # MMR: 按分数排序，逐个选入，丢弃与已选内容重叠 >85% 的
        mmr_ranked = self._mmr_deduplicate(ranked)
        return self._context(mmr_ranked)

    def _mmr_deduplicate(self, passages: list[_Passage]) -> list[_Passage]:
        """MMR 多样性去冗：按分数顺序选入，丢弃与已选内容字符重叠率超过阈值的 passage。"""
        selected: list[_Passage] = []
        selected_contents: list[set[str]] = []  # 每篇的内容 token 集合
        for passage in passages:
            tokens = set(passage.content.split())
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
                selected.append(passage)
                selected_contents.append(tokens)
        return selected

    @classmethod
    def _passages(cls, chunk: SearchChunk) -> list[_Passage]:
        ranges = cls._passage_ranges(chunk.content)
        if len(ranges) > _MAX_SEGMENTS_PER_CHUNK:
            ranges = [
                ranges[round(index * (len(ranges) - 1) / (_MAX_SEGMENTS_PER_CHUNK - 1))]
                for index in range(_MAX_SEGMENTS_PER_CHUNK)
            ]
        return [
            _Passage(
                chunk=chunk,
                content=chunk.content[start:end].strip(),
                char_start=start,
                char_end=end,
            )
            for start, end in ranges
            if chunk.content[start:end].strip()
        ]

    @staticmethod
    def _passage_ranges(content: str) -> list[tuple[int, int]]:
        if len(content) <= _CONTEXT_CHARS:
            return [(0, len(content))] if content.strip() else []
        boundaries = [match.end() for match in re.finditer(r"[\n。！？；;.!?]", content)]
        ranges: list[tuple[int, int]] = []
        start = 0
        while start < len(content):
            maximum = min(start + _CONTEXT_CHARS, len(content))
            if maximum == len(content):
                end = len(content)
            else:
                minimum = start + _CONTEXT_CHARS // 2
                nearby = [boundary for boundary in boundaries if minimum <= boundary <= maximum]
                end = nearby[-1] if nearby else maximum
            ranges.append((start, end))
            if end >= len(content):
                break
            start = max(end - _CONTEXT_OVERLAP_CHARS, start + 1)
        return ranges

    @staticmethod
    def _context(passages: list[_Passage]) -> list[dict[str, object]]:
        contexts: list[dict[str, object]] = []
        seen_evidence_ids: set[UUID] = set()
        for passage in passages:
            chunk = passage.chunk
            if chunk.evidence_id is None or chunk.evidence_id in seen_evidence_ids:
                continue
            seen_evidence_ids.add(chunk.evidence_id)
            page_number = chunk.metadata_.get("page_number")
            contexts.append(
                {
                    "evidence_id": str(chunk.evidence_id),
                    "page_number": page_number if isinstance(page_number, int) else None,
                    "content": passage.content,
                    "chunk_index": chunk.chunk_index,
                    "char_start": passage.char_start,
                    "char_end": passage.char_end,
                }
            )
        return contexts

    @staticmethod
    def _fallback_overview(
        ranked_contexts: dict[str, list[dict[str, object]]]
    ) -> list[dict[str, object]]:
        contexts: list[dict[str, object]] = []
        seen_evidence_ids: set[str] = set()
        for name in _SPECIALISTS:
            for item in ranked_contexts[name]:
                evidence_id = str(item["evidence_id"])
                if evidence_id in seen_evidence_ids:
                    continue
                seen_evidence_ids.add(evidence_id)
                contexts.append(item)
                if len(contexts) >= _OVERVIEW_CONTEXT_LIMIT:
                    return contexts
        return contexts
