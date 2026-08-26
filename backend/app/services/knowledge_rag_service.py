import asyncio
import logging
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.constants import (
    EMBEDDING_MODEL_ID,
    LLM_MODEL_ID,
    RAG_CONTEXT_LIMIT,
    RERANKER_MODEL_ID,
)
from app.core.errors import DomainError
from app.core.retrieval_config import RetrievalConfig
from app.db.models import SearchChunk
from app.db.repositories.search_repository import (
    Bm25Hit,
    KnowledgeSearchRecord,
    SearchRepository,
)
from app.integrations.ai.embedding import EmbeddingClient, EmbeddingUnavailable
from app.integrations.ai.llm import LlmUnavailable, RagLlm
from app.integrations.ai.reranker import RankerClient, RankerUnavailable
from app.integrations.vector_store import (
    VectorSearchHit,
    VectorStore,
    VectorStoreUnavailable,
)
from app.schemas.advanced import KnowledgeCitation, KnowledgeQuestionResponse
from app.schemas.evidences import EvidenceResponse
from app.services.ai_run_service import AiRunService
from app.services.audit_service import AuditService
from app.services.evidence_service import EvidenceService
from app.services.project_service import ProjectService
from app.services.query_rewrite_service import QueryRewriteResult, QueryType, rewrite_query

logger = logging.getLogger(__name__)


def _dynamic_context_limit(query_type: QueryType | None) -> int:
    """按 query_type 动态决定 rerank 后保留的 context 数。

    - FACTUAL：精确答案，6 条 context 足够（少而精）
    - 其他（DEFINITION/PROCEDURAL/COMPARISON/LIST/DEFAULT）：综合理解需要更多上下文，8 条
    - 未知类型 fallback 8（与原 RAG_CONTEXT_LIMIT 一致）
    """
    if query_type is not None and query_type.value == "factual":
        return 6
    return RAG_CONTEXT_LIMIT


def _mmr_rerank(
    candidates: list[tuple[str, float]],
    content_by_pk: dict[str, str],
    top_k: int,
    lambda_: float = 0.7,
) -> list[str]:
    """Maximal Marginal Relevance 多样性去冗余（参考 WeKnora applyMMR λ=0.7）。

    算法：
        pick c* = argmax_c [ λ * rel(c) - (1-λ) * max_sim(c, selected) ]
        其中 rel(c) = normalized rerank score, sim = 1 - jaccard(bigram(c))

    Args:
        candidates: [(pk, score)]，score 已归一化到 [0, 1]（reranker 输出）
        content_by_pk: {pk: chunk_content} 用于算 bigram jaccard
        top_k: 选几个
        lambda_: 相关性权重（0.7 = 偏相关，留 0.3 给多样性）

    Returns:
        选中的 pk 列表（保持 MMR 选择顺序，与原候选顺序无关）
    """
    if not candidates or top_k <= 0:
        return []

    # 归一化 score 到 [0, 1]
    scores = [s for _, s in candidates]
    smin, smax = min(scores), max(scores)
    score_range = smax - smin if smax > smin else 1.0

    def _norm(s: float) -> float:
        return (s - smin) / score_range

    # 预计算每个候选的 bigram set
    bigrams: dict[str, set[tuple[str, str]]] = {}
    for pk, _ in candidates:
        content = content_by_pk.get(pk, "")
        bigrams[pk] = {
            (content[i : i + 2], content[i + 1 : i + 3]) for i in range(len(content) - 2)
        }
        # bigram set 为空（极短文本）时返回空集，jaccard 视为 0

    def _sim(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0

    selected: list[str] = []
    selected_bigrams: list[set] = []
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        best_idx = 0
        best_score = -1e9
        for i, (pk, s) in enumerate(remaining):
            relevance = _norm(s)
            # 与已选最大相似度
            if selected_bigrams:
                redundancy = max(_sim(bigrams[pk], sb) for sb in selected_bigrams)
            else:
                redundancy = 0.0
            mmr_score = lambda_ * relevance - (1 - lambda_) * redundancy
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i
        chosen_pk, _ = remaining.pop(best_idx)
        selected.append(chosen_pk)
        selected_bigrams.append(bigrams[chosen_pk])

    return selected


@dataclass(frozen=True, slots=True)
class _Context:
    content: str
    content_hash: str
    citation: KnowledgeCitation
    # MMR/去重的唯一键：多个 chunk 可能共享同一 evidence，必须用 chunk_id
    chunk_id: str = ""
    # "text"（原文切块）| "faq"（合成的相似问 Q/A 块）
    content_type: str = "text"
    parent_chunk_id: UUID | None = None


class KnowledgeRagService:
    """Cited legal/case RAG with hybrid search (Vector + BM25) and RRF fusion."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        embedding_client: EmbeddingClient,
        vector_store: VectorStore,
        reranker: RankerClient,
        llm: RagLlm,
        retrieval_config: RetrievalConfig | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._search = SearchRepository(session)
        self._ai_runs = AiRunService(session)
        self._audit = AuditService(session)
        self._evidences = EvidenceService(session)
        self._projects = ProjectService(session)
        self._embedding_client = embedding_client
        self._vector_store = vector_store
        self._reranker = reranker
        self._llm = llm
        self._cfg = retrieval_config or RetrievalConfig()
        self._cfg.validate_weights()

    def answer(
        self,
        actor_id: UUID,
        role_codes: set[str],
        question: str,
        project_id: UUID | None,
    ) -> KnowledgeQuestionResponse:
        if project_id is not None:
            self._projects.get_visible(project_id, actor_id, role_codes)
        if not self._settings.ai_is_configured:
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问答 AI 服务尚未配置或暂不可用", 503)

        query_vector = self._embed_question(question)
        contexts = self._retrieve(question, query_vector, project_id, actor_id, role_codes)
        result = (
            self._no_evidence()
            if not contexts
            else self._answer(
                question, self._resolve_parent_chunks(self._rank(question, contexts))
            )
        )
        self._audit.record(
            actor_id=actor_id,
            action="ASK_KNOWLEDGE_QUESTION",
            target_type="PROJECT" if project_id is not None else "KNOWLEDGE_BASE",
            target_id=project_id,
            project_id=project_id,
            after={"joint_search": project_id is not None, "no_evidence": result.no_evidence},
        )
        self._session.commit()
        return result

    async def aanswer(
        self,
        actor_id: UUID,
        role_codes: set[str],
        question: str,
        project_id: UUID | None,
    ) -> KnowledgeQuestionResponse:
        """Async version of answer."""
        if project_id is not None:
            self._projects.get_visible(project_id, actor_id, role_codes)
        if not self._settings.ai_is_configured:
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问答 AI 服务尚未配置或暂不可用", 503)

        query_vector = await self._aembed_question(question)
        contexts = await self._aretrieve(question, query_vector, project_id, actor_id, role_codes)
        result = (
            self._no_evidence()
            if not contexts
            else await self._aanswer(
                question,
                self._resolve_parent_chunks(await self._arank(question, contexts)),
            )
        )
        self._audit.record(
            actor_id=actor_id,
            action="ASK_KNOWLEDGE_QUESTION",
            target_type="PROJECT" if project_id is not None else "KNOWLEDGE_BASE",
            target_id=project_id,
            project_id=project_id,
            after={"joint_search": project_id is not None, "no_evidence": result.no_evidence},
        )
        self._session.commit()
        return result

    async def _aprepare_retrieval(
        self,
        actor_id: UUID,
        role_codes: set[str],
        question: str,
        project_id: UUID | None,
    ) -> list[dict[str, Any]]:
        """Async version of prepare retrieval for API layer."""
        logger.info(
            "[KnowledgeRag] _aprepare_retrieval start: question=%r project_id=%s",
            question,
            project_id,
        )
        if project_id is not None:
            self._projects.get_visible(project_id, actor_id, role_codes)
        if not self._settings.ai_is_configured:
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问答 AI 服务尚未配置或暂不可用", 503)

        rewrite_result = rewrite_query(question)
        logger.info("[KnowledgeRag] rewrite done: expanded=%r", rewrite_result.expanded_query)
        query_vector = await self._aembed_question(question, rewrite_result.expanded_query)
        logger.info("[KnowledgeRag] embed done: vec_dim=%d", len(query_vector))
        contexts = await self._aretrieve(
            question, query_vector, project_id, actor_id, role_codes, rewrite_result
        )
        logger.info("[KnowledgeRag] retrieve done: contexts=%d", len(contexts))
        if not contexts:
            logger.info("[KnowledgeRag] no contexts, returning empty")
            return []

        ranked = await self._arank(question, contexts, rewrite_result)
        ranked = self._resolve_parent_chunks(ranked)
        logger.info("[KnowledgeRag] rank done: ranked=%d", len(ranked))
        result = [
            {"evidence_id": str(ctx.citation.evidence_id), "content": ctx.content} for ctx in ranked
        ]
        logger.info("[KnowledgeRag] _aprepare_retrieval done: result=%d", len(result))
        return result

    def _retrieve(
        self,
        question: str,
        query_vector: list[float],
        project_id: UUID | None,
        actor_id: UUID,
        role_codes: set[str],
    ) -> list[_Context]:
        """Sync version with hybrid Vector + BM25 search（语义与 _aretrieve 一致）."""
        rewrite_result = rewrite_query(question)
        try:
            knowledge_hits = self._vector_store.search_knowledge(
                query_vector, self._cfg.embedding_top_k
            )
            # 与异步路径一致：cosine 低于阈值视为噪声
            knowledge_hits = [
                h
                for h in knowledge_hits
                if h.score is not None and h.score >= self._cfg.vector_threshold
            ]
            project_hits = (
                self._vector_store.search(query_vector, str(project_id), self._cfg.embedding_top_k)
                if project_id is not None
                else []
            )
            logger.info("[RAG] vector done: knowledge=%d", len(knowledge_hits))
        except VectorStoreUnavailable as exc:
            raise DomainError("VECTOR_STORE_UNAVAILABLE", "向量检索服务暂不可用", 503) from exc

        # Hybrid: BM25 multi-query search (both enterprise and project chunks)
        expanded_query = rewrite_result.expanded_query
        all_bm25: list[Bm25Hit] = []
        # 企业知识库 BM25（含 multi_query 扩展）
        for q in [expanded_query, *rewrite_result.multi_queries]:
            all_bm25.extend(
                self._search.search_chunks_bm25(
                    query=q,
                    chunk_type=None,
                    project_id=None,
                    top_k=self._cfg.embedding_top_k,
                )
            )
        # 项目文档走向量通路（与异步路径一致），不重复进 BM25/RRF
        # Deduplicate BM25 hits by chunk id, keep the BEST rank across query
        # variants —— multi-query 变体间累加会让通用块反复得分挤掉精确命中
        best_rank: dict[str, Bm25Hit] = {}
        for hit in all_bm25:
            key = str(hit.chunk.id)
            if key not in best_rank or hit.rank < best_rank[key].rank:
                best_rank[key] = hit
        knowledge_bm25 = sorted(best_rank.values(), key=lambda h: h.rank)
        logger.info("[RAG] BM25 done: total=%d unique=%d", len(all_bm25), len(knowledge_bm25))

        # RRF fusion (enable keyword boost for FACTUAL questions)
        use_keyword_boost = rewrite_result.query_type.value == "factual"
        vector_pks = [h.pk for h in knowledge_hits if h.pk]
        vector_content_map = (
            self._search.get_chunks_content_by_pks(vector_pks) if vector_pks else {}
        )
        fused_pks = self._rrf_fuse(
            knowledge_hits,
            knowledge_bm25,
            top_k=self._cfg.rerank_top_k,
            keyword_boost=use_keyword_boost,
            vector_content_map=vector_content_map,
            query_type=rewrite_result.query_type,
        )
        fused_pk_set = [pk for pk, _ in fused_pks]
        # 补充两路各自的头部命中：RRF 只奖励双路同高位，单路精确命中
        # （如某法条只在 BM25 rank 13）会被通用块挤出融合窗口，重排器看不到
        for h in sorted(
            (h for h in knowledge_hits if h.pk),
            key=lambda h: h.score if h.score is not None else 0.0,
            reverse=True,
        )[:5]:
            if h.pk not in fused_pk_set:
                fused_pk_set.append(h.pk)
        for h in knowledge_bm25[:5]:
            key = str(h.chunk.id)
            if key not in fused_pk_set:
                fused_pk_set.append(key)
        logger.info("[RAG] RRF fused: %d (+leg heads = %d)", len(fused_pks), len(fused_pk_set))

        knowledge_by_pk = {
            str(record.chunk.id): record
            for record in self._search.list_visible_knowledge_chunks(fused_pk_set)
        }
        contexts = [
            self._knowledge_context(knowledge_by_pk[pk])
            for pk in fused_pk_set
            if pk in knowledge_by_pk
        ]

        if project_id is None:
            return contexts

        # cosine similarity 越高越好（1=完美匹配），用 >= threshold 保留高相关性结果
        project_filtered = [
            h for h in project_hits if h.score is not None and h.score >= self._cfg.vector_threshold
        ]
        # cosine similarity 越高越好，按分数降序取 top_k
        project_filtered.sort(key=lambda h: h.score, reverse=True)
        project_candidates = project_filtered[: self._cfg.rerank_top_k]
        project_pks = [h.pk for h in project_candidates]

        project_by_pk = {
            str(chunk.id): chunk
            for chunk in self._search.list_visible_project_chunks(project_id, project_pks)
        }
        for hit in project_candidates:
            chunk = project_by_pk.get(hit.pk)
            if chunk is None or chunk.evidence_id is None:
                continue
            try:
                evidence = self._evidences.get_visible(chunk.evidence_id, actor_id, role_codes)
            except DomainError:
                continue
            contexts.append(self._project_context(chunk, evidence))
        return contexts

    async def _aretrieve(
        self,
        question: str,
        query_vector: list[float],
        project_id: UUID | None,
        actor_id: UUID,
        role_codes: set[str],
        rewrite_result: QueryRewriteResult | None = None,
    ) -> list[_Context]:
        """Async version of _retrieve with hybrid Vector + BM25 search."""
        # Query rewrite (skip if already provided by caller)
        if rewrite_result is None:
            rewrite_result = rewrite_query(question)
        expanded_query = rewrite_result.expanded_query
        logger.info(
            "[RAG] query: type=%s expanded=%r multi=%s",
            rewrite_result.query_type.value,
            expanded_query,
            list(rewrite_result.multi_queries),
        )

        # HyDE: for FACTUAL questions, generate hypothetical answer to improve retrieval
        hyde_vector: list[float] | None = None
        if rewrite_result.query_type.value == "factual":
            try:
                hyde_text = await self._agenerate_hyde_answer(question)
                if hyde_text:
                    hyde_vectors = await self._aembed_question(hyde_text, None)
                    hyde_vector = hyde_vectors
                    logger.info("[RAG] HyDE generated: %s", hyde_text[:50])
            except Exception as exc:
                logger.warning("[RAG] HyDE failed: %s", exc)

        try:
            # PgVectorStore 是同步实现，丢线程池避免阻塞事件循环
            knowledge_hits = await asyncio.to_thread(
                self._vector_store.search_knowledge,
                query_vector, self._cfg.embedding_top_k,
            )
            # vector_threshold 过滤：cosine similarity 低于阈值视为噪声，
            # 与 project_hits 过滤逻辑保持一致
            knowledge_hits = [
                h
                for h in knowledge_hits
                if h.score is not None and h.score >= self._cfg.vector_threshold
            ]
            project_hits = (
                await asyncio.to_thread(
                    self._vector_store.search,
                    query_vector, str(project_id), self._cfg.embedding_top_k,
                )
                if project_id is not None
                else []
            )
            logger.info("[RAG] vector done: knowledge=%d", len(knowledge_hits))
        except VectorStoreUnavailable as exc:
            raise DomainError("VECTOR_STORE_UNAVAILABLE", "向量检索服务暂不可用", 503) from exc

        # Additional vector search using HyDE hypothetical answer (deduplicate vs main hits)
        if hyde_vector is not None:
            try:
                hyde_hits = await asyncio.to_thread(
                    self._vector_store.search_knowledge,
                    hyde_vector, self._cfg.embedding_top_k,
                )
                logger.info("[RAG] HyDE vector done: %d", len(hyde_hits))
                seen_pks = {h.pk for h in knowledge_hits if h.pk}
                for hit in hyde_hits:
                    if hit.pk and hit.pk not in seen_pks:
                        knowledge_hits.append(hit)
                        seen_pks.add(hit.pk)
            except VectorStoreUnavailable:
                pass  # HyDE search failure is non-fatal

        # Hybrid: BM25 multi-query parallel search + RRF fusion
        all_bm25_hits: list[Bm25Hit] = []
        all_bm25_hits.extend(
            await asyncio.to_thread(
                self._search.search_chunks_bm25,
                query=expanded_query,
                chunk_type=None,
                project_id=None,
                top_k=self._cfg.embedding_top_k,
            )
        )
        # Parallel search each multi-query
        for mq in rewrite_result.multi_queries:
            hits = await asyncio.to_thread(
                self._search.search_chunks_bm25,
                query=mq,
                chunk_type=None,
                project_id=None,
                top_k=self._cfg.embedding_top_k,
            )
            all_bm25_hits.extend(hits)

        # Deduplicate BM25 hits by chunk id, keep the BEST rank（同同步路径）
        best_rank: dict[str, Bm25Hit] = {}
        for hit in all_bm25_hits:
            key = str(hit.chunk.id)
            if key not in best_rank or hit.rank < best_rank[key].rank:
                best_rank[key] = hit
        unique_bm25 = sorted(best_rank.values(), key=lambda h: h.rank)

        logger.info("[RAG] BM25 total=%d unique=%d", len(all_bm25_hits), len(unique_bm25))

        # RRF fusion: combine vector and BM25 rankings
        # Enable keyword boost for FACTUAL questions to improve date/number retrieval
        use_keyword_boost = rewrite_result.query_type.value == "factual"
        # 为 vector hits 批量取 content，让 keyword boost 同时覆盖向量召回路径
        vector_pks = [h.pk for h in knowledge_hits if h.pk]
        vector_content_map = (
            await asyncio.to_thread(self._search.get_chunks_content_by_pks, vector_pks)
            if vector_pks
            else {}
        )
        fused_pks = self._rrf_fuse(
            knowledge_hits,
            unique_bm25,
            top_k=self._cfg.rerank_top_k,
            keyword_boost=use_keyword_boost,
            vector_content_map=vector_content_map,
            query_type=rewrite_result.query_type,
        )
        fused_pk_set = [pk for pk, _ in fused_pks]
        # 补充两路各自的头部命中（同同步路径）
        for h in sorted(
            (h for h in knowledge_hits if h.pk),
            key=lambda h: h.score if h.score is not None else 0.0,
            reverse=True,
        )[:5]:
            if h.pk not in fused_pk_set:
                fused_pk_set.append(h.pk)
        for h in unique_bm25[:5]:
            key = str(h.chunk.id)
            if key not in fused_pk_set:
                fused_pk_set.append(key)
        logger.info("[RAG] RRF fused: %d (+leg heads = %d)", len(fused_pks), len(fused_pk_set))

        # Build contexts from fused results
        knowledge_by_pk = {
            str(record.chunk.id): record
            for record in self._search.list_visible_knowledge_chunks(fused_pk_set)
        }
        contexts = [
            self._knowledge_context(knowledge_by_pk[pk])
            for pk in fused_pk_set
            if pk in knowledge_by_pk
        ]

        # Project chunks: vector search only (BM25 for project optional)
        if project_id is None:
            return contexts

        # Filter project vector hits by threshold
        # cosine similarity 越高越好（1=完美匹配），用 >= threshold 保留高相关性结果
        project_filtered = [
            h for h in project_hits if h.score is not None and h.score >= self._cfg.vector_threshold
        ]
        # cosine similarity 越高越好，按分数降序取 top_k
        project_filtered.sort(key=lambda h: h.score, reverse=True)
        project_candidates = project_filtered[: self._cfg.rerank_top_k]
        project_pks = [h.pk for h in project_candidates]

        project_by_pk = {
            str(chunk.id): chunk
            for chunk in self._search.list_visible_project_chunks(project_id, project_pks)
        }
        for hit in project_candidates:
            chunk = project_by_pk.get(hit.pk)
            if chunk is None or chunk.evidence_id is None:
                continue
            try:
                evidence = self._evidences.get_visible(chunk.evidence_id, actor_id, role_codes)
            except DomainError:
                continue
            contexts.append(self._project_context(chunk, evidence))
        return contexts

    def _embed_question(self, question: str) -> list[float]:
        rewrite_result = rewrite_query(question)
        embed_query = rewrite_result.expanded_query
        logger.info("[RAG] rewrite: q=%s exp=%s", question, embed_query)

        run = self._ai_runs.start_call(
            task_id=None,
            scene="knowledge_rag_query_embedding",
            model_id=EMBEDDING_MODEL_ID,
            input_payload={"question": question, "expanded_query": embed_query},
            evidence_ids=[],
        )
        started = perf_counter()
        try:
            logger.info("[RAG] embed query: %s", embed_query[:100])
            vectors = self._embedding_client.embed([embed_query])
            if len(vectors) != 1:
                raise EmbeddingUnavailable("query embedding count is invalid")
            logger.info("[RAG] embed done: dim=%d", len(vectors[0]))
        except EmbeddingUnavailable as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_UNAVAILABLE", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问题向量服务暂不可用", 503) from exc
        except Exception as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_FAILED", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问题向量服务暂不可用", 503) from exc
        self._ai_runs.complete_call(run, vectors, started)
        return vectors[0]

    async def _agenerate_hyde_answer(self, question: str) -> str | None:
        """Generate a brief hypothetical answer for HyDE retrieval (FACTUAL questions only).

        走 LangChainLlmClient.chat + AiRunService 埋点；保持 non-fatal 语义：
        任何错误都返回 None，由 _aretrieve 外层 logger.warning 兜底。
        """
        run = None
        started = perf_counter()
        try:
            run = self._ai_runs.start_call(
                task_id=None,
                scene="knowledge_rag_query_hyde",
                model_id=LLM_MODEL_ID,
                input_payload={"question": question},
                evidence_ids=[],
            )
        except Exception as exc:
            # 审计埋点失败不应阻塞 HyDE —— 跳过 audit 继续走 LLM 调用
            logger.warning("[RAG] HyDE start_call failed (skip audit): %s", exc)

        try:
            # 同步 hyde_answer 丢线程池，避免阻塞 event loop
            text = await asyncio.to_thread(
                self._llm.hyde_answer, question, max_tokens=100, temperature=0.3
            )
        except LlmUnavailable as exc:
            if run is not None:
                self._ai_runs.fail_call(run, "AI_SERVICE_UNAVAILABLE", started)
            logger.warning("[RAG] HyDE LLM unavailable (non-fatal): %s", exc)
            return None
        except Exception as exc:
            if run is not None:
                self._ai_runs.fail_call(run, "AI_SERVICE_FAILED", started)
            logger.warning("[RAG] HyDE unknown error (non-fatal): %s", exc)
            return None

        if run is not None:
            self._ai_runs.complete_call(run, {"hyde_text": text}, started)
        return text or None

    async def _aembed_question(self, question: str, embed_query: str | None = None) -> list[float]:
        """Async 版本；embed_query 为空时通过 rewrite_query 计算。"""
        from app.integrations.ai.embedding import AsyncEmbeddingClient

        if embed_query is None:
            rewrite_result = rewrite_query(question)
            embed_query = rewrite_result.expanded_query
            logger.info("[RAG] rewrite: q=%s exp=%s", question, embed_query)

        run = self._ai_runs.start_call(
            task_id=None,
            scene="knowledge_rag_query_embedding",
            model_id=EMBEDDING_MODEL_ID,
            input_payload={"question": question, "expanded_query": embed_query},
            evidence_ids=[],
        )
        started = perf_counter()
        try:
            logger.info("[RAG] embed query: %s", embed_query[:100])
            # Use async client if available
            if isinstance(self._embedding_client, AsyncEmbeddingClient):
                vectors = await self._embedding_client.embed([embed_query])
            else:
                vectors = await asyncio.to_thread(self._embedding_client.embed, [embed_query])
            if len(vectors) != 1:
                raise EmbeddingUnavailable("query embedding count is invalid")
            logger.info("[RAG] embed done: dim=%d", len(vectors[0]))
        except EmbeddingUnavailable as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_UNAVAILABLE", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问题向量服务暂不可用", 503) from exc
        except Exception as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_FAILED", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问题向量服务暂不可用", 503) from exc
        self._ai_runs.complete_call(run, vectors, started)
        return vectors[0]

    def _rank(self, question: str, contexts: list[_Context]) -> list[_Context]:
        rewrite_result = rewrite_query(question)
        rerank_query = rewrite_result.rerank_query
        logger.info("[RAG] rerank: ctx=%d q=%s", len(contexts), rerank_query[:50])

        run = self._ai_runs.start_call(
            task_id=None,
            scene="knowledge_rag_rerank",
            model_id=RERANKER_MODEL_ID,
            input_payload={
                "question": question,
                "rerank_query": rerank_query,
                "chunk_hashes": [context.content_hash for context in contexts],
            },
            evidence_ids=[context.citation.evidence_id for context in contexts],
        )
        started = perf_counter()
        try:
            scores = self._reranker.rerank(rerank_query, [context.content for context in contexts])
            if len(scores) != len(contexts):
                raise RankerUnavailable("reranker result count is invalid")
            logger.info("[RAG] rerank done: scores=%s", scores[:5])
        except RankerUnavailable as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_UNAVAILABLE", started)
            raise DomainError(
                "AI_SERVICE_UNAVAILABLE", "重排服务暂不可用，无法生成无引文回答", 503
            ) from exc
        except Exception as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_FAILED", started)
            raise DomainError(
                "AI_SERVICE_UNAVAILABLE", "重排服务暂不可用，无法生成无引文回答", 503
            ) from exc
        self._ai_runs.complete_call(run, scores, started)
        # FAQ 合成块降权：取值类查询（金额/期限/人数）优先原文法条
        _FAQ_RERANK_PENALTY = 0.15
        scored_pairs = [
            (c, float(sc) - (_FAQ_RERANK_PENALTY if c.content_type == "faq" else 0.0))
            for c, sc in zip(contexts, scores, strict=True)
        ]
        contexts = [c for c, _ in scored_pairs]
        scores = [sc for _, sc in scored_pairs]
        # MMR 多样性去冗余：rerank 后选 top_k，再按 bigram jaccard 去冗余
        final_k = _dynamic_context_limit(rewrite_result.query_type)
        ctx_by_id = {c.chunk_id: c for c in contexts}
        # 候选 = 全量 contexts 的 (pk, score)，MMR 选出 top_k 个 pk
        candidates = [
            (c.chunk_id, float(sc))
            for c, sc in zip(contexts, scores, strict=True)
        ]
        content_map = {c.chunk_id: c.content for c in contexts}
        selected_pks = _mmr_rerank(candidates, content_map, top_k=final_k)
        ranked_contexts = [ctx_by_id[pk] for pk in selected_pks if pk in ctx_by_id]
        logger.info("[RAG] final contexts: %d (MMR)", len(ranked_contexts))
        return ranked_contexts

    async def _arank(
        self,
        question: str,
        contexts: list[_Context],
        rewrite_result: QueryRewriteResult | None = None,
    ) -> list[_Context]:
        """Async version of _rank."""
        from app.integrations.ai.reranker import AsyncRankerClient

        if rewrite_result is None:
            rewrite_result = rewrite_query(question)
        rerank_query = rewrite_result.rerank_query
        logger.info("[RAG] rerank: ctx=%d query=%s", len(contexts), rerank_query[:50])

        run = self._ai_runs.start_call(
            task_id=None,
            scene="knowledge_rag_rerank",
            model_id=RERANKER_MODEL_ID,
            input_payload={
                "question": question,
                "rerank_query": rerank_query,
                "chunk_hashes": [context.content_hash for context in contexts],
            },
            evidence_ids=[context.citation.evidence_id for context in contexts],
        )
        started = perf_counter()
        try:
            # Use async reranker if available
            if isinstance(self._reranker, AsyncRankerClient):
                scores = await self._reranker.rerank(rerank_query, [c.content for c in contexts])
            else:
                scores = await asyncio.to_thread(
                    self._reranker.rerank, rerank_query, [c.content for c in contexts]
                )
            if len(scores) != len(contexts):
                raise RankerUnavailable("reranker result count is invalid")
            logger.info("[RAG] rerank done: scores=%s", scores[:5])
        except RankerUnavailable as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_UNAVAILABLE", started)
            raise DomainError(
                "AI_SERVICE_UNAVAILABLE", "重排服务暂不可用，无法生成无引文回答", 503
            ) from exc
        except Exception as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_FAILED", started)
            raise DomainError(
                "AI_SERVICE_UNAVAILABLE", "重排服务暂不可用，无法生成无引文回答", 503
            ) from exc
        self._ai_runs.complete_call(run, scores, started)
        # FAQ 合成块降权：取值类查询（金额/期限/人数）优先原文法条
        _FAQ_RERANK_PENALTY = 0.15
        scored_pairs = [
            (c, float(sc) - (_FAQ_RERANK_PENALTY if c.content_type == "faq" else 0.0))
            for c, sc in zip(contexts, scores, strict=True)
        ]
        contexts = [c for c, _ in scored_pairs]
        scores = [sc for _, sc in scored_pairs]
        # MMR 多样性去冗余
        final_k = _dynamic_context_limit(rewrite_result.query_type)
        ctx_by_id = {c.chunk_id: c for c in contexts}
        candidates = [
            (c.chunk_id, float(sc))
            for c, sc in zip(contexts, scores, strict=True)
        ]
        content_map = {c.chunk_id: c.content for c in contexts}
        selected_pks = _mmr_rerank(candidates, content_map, top_k=final_k)
        ranked_contexts = [ctx_by_id[pk] for pk in selected_pks if pk in ctx_by_id]
        logger.info("[RAG] final contexts: %d (MMR)", len(ranked_contexts))
        return ranked_contexts

    def _answer(self, question: str, contexts: list[_Context]) -> KnowledgeQuestionResponse:
        run = self._ai_runs.start_call(
            task_id=None,
            scene="knowledge_rag_answer",
            model_id=LLM_MODEL_ID,
            input_payload={
                "question": question,
                "chunk_hashes": [context.content_hash for context in contexts],
            },
            evidence_ids=[context.citation.evidence_id for context in contexts],
        )
        started = perf_counter()
        try:
            draft = self._llm.answer_question(
                question,
                [
                    {
                        "evidence_id": str(context.citation.evidence_id),
                        "content": context.content,
                    }
                    for context in contexts
                ],
            )
        except LlmUnavailable as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_UNAVAILABLE", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问答生成服务暂不可用", 503) from exc
        except Exception as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_FAILED", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问答生成服务暂不可用", 503) from exc

        contexts_by_evidence = {context.citation.evidence_id: context for context in contexts}
        cited = list(dict.fromkeys(draft.evidence_ids))
        if not cited or any(evidence_id not in contexts_by_evidence for evidence_id in cited):
            self._ai_runs.invalidate_call(run, "INVALID_EVIDENCE_CITATION", started)
            return self._no_evidence()
        self._ai_runs.complete_call(run, draft.model_dump(mode="json"), started)
        return KnowledgeQuestionResponse(
            answer=draft.answer,
            citations=[contexts_by_evidence[evidence_id].citation for evidence_id in cited],
            no_evidence=False,
        )

    async def _aanswer(self, question: str, contexts: list[_Context]) -> KnowledgeQuestionResponse:
        """Async version of _answer. LLM call is sync but runs in thread pool."""

        run = self._ai_runs.start_call(
            task_id=None,
            scene="knowledge_rag_answer",
            model_id=LLM_MODEL_ID,
            input_payload={
                "question": question,
                "chunk_hashes": [context.content_hash for context in contexts],
            },
            evidence_ids=[context.citation.evidence_id for context in contexts],
        )
        started = perf_counter()
        try:
            # Run sync LLM call in thread pool
            draft = await asyncio.to_thread(
                self._llm.answer_question,
                question,
                [
                    {
                        "evidence_id": str(context.citation.evidence_id),
                        "content": context.content,
                    }
                    for context in contexts
                ],
            )
        except LlmUnavailable as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_UNAVAILABLE", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问答生成服务暂不可用", 503) from exc
        except Exception as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_FAILED", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问答生成服务暂不可用", 503) from exc

        contexts_by_evidence = {context.citation.evidence_id: context for context in contexts}
        cited = list(dict.fromkeys(draft.evidence_ids))
        if not cited or any(evidence_id not in contexts_by_evidence for evidence_id in cited):
            self._ai_runs.invalidate_call(run, "INVALID_EVIDENCE_CITATION", started)
            return self._no_evidence()
        self._ai_runs.complete_call(run, draft.model_dump(mode="json"), started)
        return KnowledgeQuestionResponse(
            answer=draft.answer,
            citations=[contexts_by_evidence[evidence_id].citation for evidence_id in cited],
            no_evidence=False,
        )

    # ------------------------------------------------------------------
    # Hybrid Search: Vector + BM25 with RRF Fusion
    # ------------------------------------------------------------------

    # Keyword patterns for FACTUAL question boost (Chinese legal domain)
    _KEYWORD_PATTERNS = (
        # Year-month patterns (e.g., 2017年10月, 2017-10)
        r"\d{4}年\d{1,2}月",
        r"\d{4}-\d{2}",
        r"\d{4}\.\d{2}",
        # Numbered regulation shortcuts (87号令, 18号令, etc.)
        r"\d{2,4}号令",
        r"\d+号",
        # Date/action keywords
        r"施行",
        r"发布",
        r"公布",
        r"生效",
        r"实施",
        r"年\d{1,2}月\d{1,2}日",
        r"日起?",
        r"自.*起",
    )

    def _rrf_fuse(
        self,
        vector_hits: list[VectorSearchHit],
        bm25_hits: list,  # list[Bm25Hit]
        top_k: int | None = None,
        keyword_boost: bool = False,
        vector_content_map: dict[str, str] | None = None,
        query_type: QueryType | None = None,
    ) -> list[tuple[str, float]]:
        """Fuse vector and BM25 rankings using RRF (Reciprocal Rank Fusion).

        Returns list of (pk, fused_score) sorted descending by fused_score.
        When keyword_boost=True, applies score boost to chunks containing
        factual keywords (dates, regulation numbers, action verbs).
        vector_content_map: optional {pk: content} for vector hits so they
        can also benefit from keyword boost (otherwise skipped).

        query_type: 自适应权重
            - FACTUAL: keyword 权重 0.3 → 0.5（事实问题更依赖精确匹配）
            - 其他：保持 RetrievalConfig 默认（0.7 / 0.3）

        短路规则（参考 WeKnora knowledgebase_search.go）：
            - 仅 vector：直接按 score 排序
            - 仅 bm25：直接按 rank 排序
            - 都没有：返回空列表
        """
        import re as _re

        if top_k is None:
            top_k = self._cfg.rerank_top_k

        # 短路 1：仅 vector 通路 → 直接按 cosine score 排序
        if vector_hits and not bm25_hits:
            sorted_vec = sorted(
                (h for h in vector_hits if h.pk),
                key=lambda h: h.score or 0.0,
                reverse=True,
            )
            return [(h.pk, h.score or 0.0) for h in sorted_vec[:top_k]]

        # 短路 2：仅 bm25 通路 → 直接按 ts_rank 排序（保持输入顺序即 rank 升序）
        if bm25_hits and not vector_hits:
            return [
                (str(h.chunk.id), float(h.bm25_score))
                for h in bm25_hits[:top_k]
                if h.chunk.id
            ]

        # 短路 3：双通路都为空
        if not vector_hits and not bm25_hits:
            return []

        # 自适应权重：FACTUAL 类问题偏 keyword，其他保持默认
        if query_type is not None and query_type.value == "factual":
            vw = 0.5
            kw = 0.5
        else:
            vw = self._cfg.rrf_vector_weight
            kw = self._cfg.rrf_keyword_weight

        rrf: dict[str, float] = {}

        # Vector: rank by cosine similarity score (higher = better)
        for rank, hit in enumerate(
            sorted(vector_hits, key=lambda h: h.score if h.score is not None else 0.0, reverse=True)
        ):
            if hit.pk:
                rrf[hit.pk] = rrf.get(hit.pk, 0.0) + vw / (self._cfg.rrf_k + rank + 1)

        # BM25: rank by rank (already ordered by ts_rank desc)
        for hit in bm25_hits:
            if hit.chunk.id:
                pk = str(hit.chunk.id)
                rrf[pk] = rrf.get(pk, 0.0) + kw / (self._cfg.rrf_k + hit.rank + 1)

        # Keyword boost: for FACTUAL questions, give extra weight to chunks
        # containing date/number/action keywords (no Chinese tokenization needed)
        if keyword_boost and self._cfg.keyword_boost_enabled:
            # Collect chunk content for boost calculation: BM25 自带 content，
            # vector hits 需通过 vector_content_map 由调用方补齐
            chunk_content_map: dict[str, str] = {}
            for hit in bm25_hits:
                if hit.chunk.id:
                    chunk_content_map[str(hit.chunk.id)] = hit.chunk.content
            for pk, content in (vector_content_map or {}).items():
                if pk not in chunk_content_map:
                    chunk_content_map[pk] = content

            boost_patterns = [_re.compile(p) for p in self._KEYWORD_PATTERNS]

            def keyword_score(content: str) -> float:
                """Count keyword matches, return boost multiplier."""
                if not content:
                    return 0.0
                matches = sum(1 for p in boost_patterns if p.search(content))
                return matches * 0.5  # +0.5 per keyword match

            for pk in rrf:
                content = chunk_content_map.get(pk, "")
                boost = keyword_score(content)
                if boost > 0:
                    rrf[pk] += boost
                    logger.debug("[RRF] keyword boost: pk=%s +%.2f", pk[:8], boost)

        sorted_fused = sorted(rrf.items(), key=lambda item: item[1], reverse=True)
        logger.info(
            "[RAG] RRF: %d pks from vec=%d bm25=%d keyword_boost=%s vec_content=%d",
            len(sorted_fused),
            len(vector_hits),
            len(bm25_hits),
            keyword_boost,
            len(vector_content_map or {}),
        )
        return sorted_fused[:top_k]

    @staticmethod
    def _knowledge_context(record: KnowledgeSearchRecord) -> _Context:
        return _Context(
            content=record.chunk.content,
            content_hash=record.chunk.content_hash,
            chunk_id=str(record.chunk.id),
            content_type=record.chunk.content_type or "text",
            parent_chunk_id=record.chunk.parent_chunk_id,
            citation=KnowledgeCitation(
                evidence_id=record.evidence.id,
                document_id=record.document.id,
                document_version_id=record.document_version.id,
                file_name=record.document_version.file_name,
                version_no=record.document_version.version_no,
                page_number=record.evidence.page_number,
                quoted_text=record.evidence.quoted_text,
                scope="KNOWLEDGE",
                knowledge_entry_id=record.entry.id,
                knowledge_version_id=record.knowledge_version.id,
            ),
        )

    @staticmethod
    def _project_context(chunk: SearchChunk, evidence: EvidenceResponse) -> _Context:
        return _Context(
            content=chunk.content,
            content_hash=chunk.content_hash,
            chunk_id=str(chunk.id),
            content_type=chunk.content_type or "text",
            parent_chunk_id=chunk.parent_chunk_id,
            citation=KnowledgeCitation(
                evidence_id=evidence.id,
                document_id=evidence.document_id,
                document_version_id=evidence.document_version_id,
                file_name=evidence.file_name,
                version_no=evidence.version_no,
                page_number=evidence.page_number,
                quoted_text=evidence.quoted_text,
                scope="PROJECT",
                knowledge_entry_id=None,
                knowledge_version_id=None,
            ),
        )

    def _resolve_parent_chunks(self, contexts: list[_Context]) -> list[_Context]:
        """父子块解析（WeKnora MatchTypeParentChunk 语义）。

        - 同父多子命中：合并为一条 context，父块内容只出现一次
        - 独子命中：父块内容 + 子块内容拼接
        保持输入的排序顺序不变。
        """
        if not contexts:
            return contexts

        parent_ids = {c.parent_chunk_id for c in contexts if c.parent_chunk_id}
        if not parent_ids:
            return contexts

        parent_map: dict[UUID, SearchChunk] = {}
        for pid in parent_ids:
            parent = self._search.get_chunk(pid)
            if parent is not None:
                parent_map[pid] = parent

        result: list[_Context] = []
        group_sizes: dict[UUID, int] = {}
        for ctx in contexts:
            pid = ctx.parent_chunk_id
            if pid and pid in parent_map:
                group_sizes[pid] = group_sizes.get(pid, 0) + 1

        emitted: set[UUID] = set()
        for ctx in contexts:
            pid = ctx.parent_chunk_id
            if pid and pid in parent_map:
                if pid in emitted:
                    # 同父兄弟：跳过（内容已并入载体的父块全文）
                    continue
                emitted.add(pid)
                parent = parent_map[pid]
                if group_sizes[pid] > 1:
                    # 同父多子：父块全文只出现一次
                    ctx = replace(ctx, content=parent.content)
                else:
                    ctx = replace(ctx, content=f"{parent.content}\n{ctx.content}")
            result.append(ctx)
        return result

    def _no_evidence(self) -> KnowledgeQuestionResponse:
        return KnowledgeQuestionResponse(
            answer="抱歉，暂时未找到相关内容。", citations=[], no_evidence=True
        )
