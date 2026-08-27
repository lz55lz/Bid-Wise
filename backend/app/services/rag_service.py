"""rag_service.py — 优化版 Unified RAG service (Vector + BM25 + RRF + Rerank + 8-step merge)."""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from logging import getLogger
from time import perf_counter
from typing import Any
from uuid import UUID

import jieba
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
from app.db.repositories.search_repository import Bm25Hit, KnowledgeSearchRecord, SearchRepository
from app.db.repositories.session_repository import MessageRepository
from app.integrations.ai.embedding import (
    AsyncEmbeddingClient,
    EmbeddingClient,
    EmbeddingUnavailable,
)
from app.integrations.ai.llm import (
    LlmAuthenticationFailed,
    LlmRateLimited,
    LlmTimeout,
    LlmUnavailable,
    RagLlm,
)
from app.integrations.ai.reranker import (
    AsyncRankerClient,
    RankerClient,
    RankerUnavailable,
)
from app.integrations.vector_store import (
    VectorSearchHit,
    VectorStore,
    VectorStoreUnavailable,
)
from app.schemas.evidences import EvidenceResponse
from app.schemas.rag import RagAnswerResponse, RagCitation
from app.services.ai_run_service import AiRunService
from app.services.evidence_service import EvidenceService
from app.services.merge_service import merge_sequential_chunks
from app.services.project_service import ProjectService
from app.services.query_rewrite_service import QueryType, rewrite_query

logger = getLogger(__name__)

# ─── 阈值常量（来自 WeKnora） ─────────────────────────────────────────────────
_NEIGHBOR_MIN_CHARS = 350        # <350 rune 时扩展
_NEIGHBOR_MAX_CHARS = 850        # 上限 850 rune
_CONTENT_SIG_OVERLAP_THRESHOLD = 0.85   # token 重合率 ≥0.85 视为近重复
_MMR_LAMBDA = 0.7
_HISTORY_MAX_INJECT = 3
_HISTORY_MIN_SIMILARITY = 0.15
_HISTORY_SCORE_DISCOUNT = 0.6
_MIN_RERANK_THRESHOLD = 0.3
# 答案生成 prompt 上下文总字符预算（WeKnora into_chat_message.go 风格）：
# 父子块体系下父块平均 637 字符，超 10 条即 6.4K 字符 + 引文 metadata
# 截断优先级：按 rerank composite_score 由低到高丢
_ANSWER_PROMPT_MAX_CHARS = 8000
_THRESHOLD_DEGRADE_RATIO = 0.7
_COMPOSITE_RERANK_W = 0.6
_COMPOSITE_BASE_W = 0.3
_COMPOSITE_SOURCE_W = 0.1

# ─── Markdown / 结构噪声清洗正则（WeKnora rerank.go cleanPassageForRerank） ──
_RE_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^()\s]*(?:\([^)]*\)[^()\s]*)*\)")
_RE_LINKED_IMAGE = re.compile(
    r"\[!\[([^\]]*)\]\(([^()\s]*(?:\([^)]*\)[^()\s]*)*)\)\]"
    r"\([^()\s]*(?:\([^)]*\)[^()\s]*)*\)"
)
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\([^()\s]*(?:\([^)]*\)[^()\s]*)*\)")
_RE_RAW_URL = re.compile(r"https?://[^\s)\]>]+")
_RE_CODE_BLOCK = re.compile(r"(?s)```[^\r\n]*\r?\n(.*?)\r?\n?```")
_RE_LATEX = re.compile(r"(?s)\$\$(.*?)\$\$")
_RE_TABLE_SEP = re.compile(r"(?m)^[ \t]*\|[ \t:|-]+\|[ \t]*$")
_RE_TABLE_ROW = re.compile(r"(?m)^[ \t]*\|(.+?)\|[ \t]*$")
_RE_HEADING = re.compile(r"(?m)^#{1,6}\s+")
_RE_BLOCKQUOTE = re.compile(r"(?m)^>\s?")
_RE_BOLD3 = re.compile(r"\*{3}(.+?)\*{3}")
_RE_BOLD2 = re.compile(r"\*{2}(.+?)\*{2}")
_RE_BOLD1 = re.compile(r"\*(.+?)\*")
_RE_EXCESS_NL = re.compile(r"\n{3,}")
_RE_LIST_MARKER = re.compile(r"(?m)^[\t ]*(?:[-*+]|\d+\.)\s+")
_RE_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")

# 事实性关键词模式（编译一次）
_KEYWORD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?"),
    re.compile(r"第[一二三四五六七八九十百千万\d]+条"),
    re.compile(r"[一二三四五六七八九十]+、"),
    re.compile(r"[^一-龥a-zA-Z0-9]{2,}"),
    re.compile(r"招标|投标|中标|采购|合同|资质"),
    re.compile(r"万元|元/吨|米|平方米|立方米"),
    re.compile(r"中华人民共和国|^GB[/-]?\d+"),
    re.compile(r"实施"),
    re.compile(r"年\d{1,2}月\d{1,2}日"),
    re.compile(r"日起?"),
    re.compile(r"自.*起"),
)


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def _clean_passage_for_rerank(text: str) -> str:
    """清除 markdown/结构噪声，保留语义内容（WeKnora rerank.go cleanPassageForRerank）。"""
    text = _RE_CODE_BLOCK.sub(r"\1", text)
    text = _RE_LATEX.sub(r"\1", text)
    text = _RE_HTML_TAG.sub("", text)
    text = _RE_LINKED_IMAGE.sub(r"![$1]($2)", text)
    text = _RE_MD_IMAGE.sub("", text)
    text = _RE_MD_LINK.sub(r"\1", text)
    text = _RE_RAW_URL.sub("", text)
    text = _RE_TABLE_SEP.sub("", text)
    text = _RE_TABLE_ROW.sub(_table_row_to_text, text)
    text = _RE_HEADING.sub("", text)
    text = _RE_BLOCKQUOTE.sub("", text)
    text = _RE_BOLD3.sub(r"\1", text)
    text = _RE_BOLD2.sub(r"\1", text)
    text = _RE_BOLD1.sub(r"\1", text)
    text = _RE_LIST_MARKER.sub("", text)
    text = _RE_EXCESS_NL.sub("\n\n", text)
    return text.strip()


def _table_row_to_text(match: re.Match) -> str:
    inner = match.group(1)
    cells = inner.split("|")
    parts = [c.strip() for c in cells if c.strip()]
    return ", ".join(parts)


def _is_all_punct(text: str) -> bool:
    """判断是否全是标点/空白符号。"""
    for char in text:
        cat = unicodedata.category(char)
        if cat not in ("Po", "Zs", "So", "Sm", "Sc", "Pd") and not char.isspace():
            return False
    return True


def _has_cjk(text: str) -> bool:
    """检测字符串是否含 CJK 表意字符。"""
    for ch in text:
        if unicodedata.category(ch) == "Lo" and "一" <= ch <= "鿿":
            return True
    return False


def _tokenize_jieba(text: str) -> frozenset[str]:
    """jieba 分词（搜索引擎模式），过滤单字和纯标点（WeKnora TokenizeSimple）。"""
    text_lower = text.lower().strip()
    if not text_lower:
        return frozenset()

    words = jieba.cut_for_search(text_lower) if _has_cjk(text_lower) else text_lower.split()

    result: set[str] = set()
    for w in words:
        w = w.strip()
        if len(w) <= 1 or _is_all_punct(w):
            continue
        result.add(w)
    return frozenset(result)


def _tokenize_simple(content: str) -> frozenset[str]:
    """简单分词：按空白符分割成 token 集合，过滤单字符和纯标点（WeKnora TokenizeSimple）。"""
    tokens: set[str] = set()
    for word in content.lower().split():
        word = word.strip()
        if len(word) > 1 and not _is_all_punct(word):
            tokens.add(word)
    return frozenset(tokens)


def _jaccard_set(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard 相似度（set 版本）。"""
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    inter = len(a & b)
    union = len(a) + len(b) - inter
    return inter / union if union > 0 else 0.0


def _content_signature(content: str) -> str:
    """归一化内容的 MD5 签名，用于近重复检测（WeKnora BuildContentSignature）。"""
    normalized = " ".join(content.lower().strip().split())
    if not normalized:
        return ""
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _content_overlap_ratio(a: str, b: str) -> float:
    """内容重合率：较小区间 token 被较大区间包含的比例（WeKnora ContentOverlapRatio）。"""
    tokens_a = _tokenize_simple(a)
    tokens_b = _tokenize_simple(b)
    if not tokens_a or not tokens_b:
        return 0.0
    small, large = (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    intersection = len(small & large)
    return intersection / len(small) if small else 0.0


def _keyword_score(content: str) -> float:
    """事实性关键词命中打分，每命中一个模式加 0.5。"""
    if not content:
        return 0.0
    return sum(0.5 for p in _KEYWORD_PATTERNS if p.search(content))


@dataclass(frozen=True, slots=True)
class _Context:
    chunk: SearchChunk
    evidence: EvidenceResponse
    base_score: float = 0.0  # RRF 融合后的原始分数，用于 Composite Score
    # LEGAL/CASE knowledge chunks：不经 project 维度的 evidence 校验，直接带 citation
    knowledge_record: KnowledgeSearchRecord | None = None


class RagService:
    """Unified RAG: hybrid search (Vector + BM25 + RRF) + rerank + 8-step merge + citation.

    合并了 RagService 和 KnowledgeRagService，统一使用 Vector + BM25 混合搜索。
    """

    def __init__(
        self,
        session: Session,
        settings: Settings,
        embedding_client: EmbeddingClient | AsyncEmbeddingClient,
        vector_store: VectorStore,
        reranker: RankerClient,
        llm: RagLlm,
        retrieval_config: RetrievalConfig | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._search = SearchRepository(session)
        self._ai_runs = AiRunService(session)
        self._evidences = EvidenceService(session)
        self._projects = ProjectService(session)
        self._embedding_client = embedding_client
        self._vector_store = vector_store
        self._reranker = reranker
        self._llm = llm
        self._cfg = retrieval_config or RetrievalConfig()
        self._cfg.validate_weights()
        self._messages = MessageRepository(session)

    # ─── 公共入口 ──────────────────────────────────────────────────────────

    def answer(
        self,
        project_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        question: str,
        session_id: str | None = None,
    ) -> RagAnswerResponse:
        _ = self._projects.get_visible(project_id, actor_id, role_codes)
        if not self._settings.ai_is_configured:
            raise DomainError(
                "AI_SERVICE_UNAVAILABLE", "问答 AI 服务尚未配置或暂不可用", 503
            )

        rewrite_result = rewrite_query(question)
        if rewrite_result.query_type in (QueryType.GREETING, QueryType.CHITCHAT):
            logger.info("[RAG] skip retrieval for %s query", rewrite_result.query_type.value)
            return self._no_evidence()

        query_vector = self._embed_question(question, rewrite_result)
        contexts = self._retrieve(
            question, query_vector, project_id, actor_id, role_codes, rewrite_result
        )
        if not contexts:
            logger.info("[RAG] no contexts retrieved for question=%r", question[:50])
            return self._no_evidence()

        ranked = self._rank(question, contexts, rewrite_result)

        # WeKnora 8 步合并流水线
        ranked = self._deduplicate_by_signature(ranked)
        ranked = self._inject_history(ranked, question, session_id)
        ranked = self._resolve_parent_chunks(ranked)
        ranked = self._merge_sequential(ranked)
        # step 6 (FAQ backfill) 已在 _visible_contexts 内完成
        ranked = self._expand_neighbors(ranked)
        ranked = self._merge_sequential(ranked)  # 7.5 邻居扩展后再合并
        ranked = self._mmr_diversify(ranked)

        return self._answer_with_citations(question, ranked[:RAG_CONTEXT_LIMIT])

    # ─── 来源权重（RRF 融合阶段） ─────────────────────────────────────────
    # 项目问答里"本项目"的条款应排在法规条文前面：法规措辞规整、关键词天然占优，
    # 不加权会被法条抢前排。enterprise 材料与法规同为补充来源，加一半。
    _SOURCE_BOOST_PROJECT = 0.15
    _SOURCE_BOOST_ENTERPRISE = 0.08

    def _build_source_boost_map(
        self, vector_hits: list, bm25_hits: list
    ) -> dict[str, float]:
        """pk -> 来源加成。BM25 命中自带 chunk 元数据；向量命中按 pk 批量回查。"""
        scopes: dict[str, tuple[str | None, UUID | None]] = {}
        for hit in bm25_hits:
            scopes[str(hit.chunk.id)] = (hit.chunk.chunk_type, hit.chunk.project_id)
        vector_pks = [h.pk for h in vector_hits if h.pk and h.pk not in scopes]
        if vector_pks:
            scopes.update(self._search.get_chunk_scopes_by_pks(vector_pks))
        boost: dict[str, float] = {}
        for pk, (chunk_type, project_id) in scopes.items():
            if project_id is not None or chunk_type == "TENDER":
                boost[pk] = self._SOURCE_BOOST_PROJECT
            elif chunk_type == "ENTERPRISE":
                boost[pk] = self._SOURCE_BOOST_ENTERPRISE
        return boost

    # ─── 检索：BM25 / Vector 收集 ──────────────────────────────────────────

    def _collect_vector_hits(
        self, query_vector: list[float], project_id: UUID
    ) -> list[VectorSearchHit]:
        try:
            project_hits = self._vector_store.search(
                query_vector, str(project_id), self._cfg.embedding_top_k
            )
            enterprise_hits = self._vector_store.search_enterprise(
                query_vector, self._cfg.embedding_top_k
            )
            # 统一检索：LEGAL/CASE 也参与 RRF 竞争，避免漏召回
            knowledge_hits = self._vector_store.search_knowledge(
                query_vector, self._cfg.embedding_top_k
            )
            return [*project_hits, *enterprise_hits, *knowledge_hits]
        except VectorStoreUnavailable as exc:
            raise DomainError(
                "VECTOR_STORE_UNAVAILABLE", "向量检索服务暂不可用", 503
            ) from exc

    def _collect_bm25_hits(
        self, rewrite_result, project_id: UUID
    ) -> list[Bm25Hit]:
        """收集项目 + 企业知识库的 BM25 命中，并对 multi_query 做扩展。"""
        queries = [rewrite_result.expanded_query, *rewrite_result.multi_queries]
        bm25_hits: list[Bm25Hit] = []
        for q in queries:
            bm25_hits.extend(
                self._search.search_chunks_bm25(
                    query=q, chunk_type=None,
                    project_id=project_id, top_k=self._cfg.embedding_top_k,
                )
            )
            bm25_hits.extend(
                self._search.search_chunks_bm25(
                    query=q, chunk_type="ENTERPRISE",
                    project_id=None, top_k=self._cfg.embedding_top_k,
                )
            )

        # 去重
        seen_ids: set[str] = set()
        unique: list[Bm25Hit] = []
        for hit in bm25_hits:
            hid = str(hit.chunk.id)
            if hid not in seen_ids:
                seen_ids.add(hid)
                unique.append(hit)
        return unique

    def _collect_chunks_by_pks(
        self, project_id: UUID, pks: list[str]
    ) -> tuple[dict[str, SearchChunk], dict[str, KnowledgeSearchRecord]]:
        """返回 (project_chunks, knowledge_records)。"""
        chunks_by_pk: dict[str, SearchChunk] = {
            str(c.id): c
            for c in self._search.list_visible_project_chunks(project_id, pks)
        }
        chunks_by_pk.update(
            (str(c.id), c)
            for c in self._search.list_visible_enterprise_chunks(project_id, pks)
        )
        # LEGAL/CASE 全局知识库 chunks：不经 project 权限校验
        knowledge_by_pk: dict[str, KnowledgeSearchRecord] = {
            str(r.chunk.id): r
            for r in self._search.list_visible_knowledge_chunks(pks)
        }
        return chunks_by_pk, knowledge_by_pk

    def _build_visible_contexts(
        self,
        project_id: UUID,
        pks: list[str],
        chunks_by_pk: dict[str, SearchChunk],
        knowledge_by_pk: dict[str, KnowledgeSearchRecord],
        actor_id: UUID,
        role_codes: set[str],
        fused_scores: dict[str, float],
    ) -> list[_Context]:
        # 批量预处理 FAQ chunks（仅 LEGAL/CASE 全局知识库）
        for record in knowledge_by_pk.values():
            chunk = record.chunk
            if chunk.content_type == "faq" and chunk.faq_metadata:
                meta = chunk.faq_metadata
                question = meta.get("standard_question") or meta.get("standardQuestion") or ""
                answers = meta.get("answers") or []
                if isinstance(answers, list) and answers:
                    answer_text = "\n".join(str(a) for a in answers)
                else:
                    answer_text = str(answers) if answers else ""
                if question or answer_text:
                    chunk.content = f"Q:{question}\nA:{answer_text}"

        contexts: list[_Context] = []
        for pk in pks:
            # 处理 project/enterprise chunks（走项目权限校验）
            if pk in chunks_by_pk:
                chunk = chunks_by_pk[pk]
                if chunk.evidence_id is None:
                    continue
                try:
                    evidence = self._evidences.get_visible_for_project(
                        chunk.evidence_id, project_id, actor_id, role_codes
                    )
                except DomainError:
                    continue
                contexts.append(
                    _Context(
                        chunk=chunk,
                        evidence=evidence,
                        base_score=fused_scores.get(pk, 0.0),
                    )
                )
            # 处理 LEGAL/CASE 全局知识库 chunks（无项目权限限制）
            elif pk in knowledge_by_pk:
                record = knowledge_by_pk[pk]
                contexts.append(
                    _Context(
                        chunk=record.chunk,
                        evidence=EvidenceResponse(
                            id=record.evidence.id,
                            source_type=record.evidence.source_type or "KNOWLEDGE",
                            document_id=record.document.id,
                            document_version_id=record.document_version.id,
                            document_node_id=record.evidence.document_node_id,
                            file_name=record.document_version.file_name,
                            version_no=record.document_version.version_no,
                            page_number=record.evidence.page_number,
                            quoted_text=record.evidence.quoted_text or "",
                            content_hash=record.evidence.content_hash or "",
                            bbox=record.evidence.bbox,
                            actor_id=actor_id,
                            role_codes=role_codes,
                        ),
                        base_score=fused_scores.get(pk, 0.0),
                        knowledge_record=record,
                    )
                )
        return contexts

    # ─── 检索：sync / async ─────────────────────────────────────────────────

    def _retrieve(
        self,
        question: str,  # noqa: ARG002  (kept for logging / future use)
        query_vector: list[float],
        project_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        rewrite_result,
    ) -> list[_Context]:
        vector_hits = self._collect_vector_hits(query_vector, project_id)
        bm25_hits = self._collect_bm25_hits(rewrite_result, project_id)

        use_keyword_boost = rewrite_result.query_type.value == "factual"
        vector_pks = [h.pk for h in vector_hits if h.pk]
        vector_content_map = (
            self._search.get_chunks_content_by_pks(vector_pks) if vector_pks else {}
        )
        source_boost_map = self._build_source_boost_map(vector_hits, bm25_hits)
        # keyword_boost（每命中 +0.5）会把 +0.15 的来源权重压没，等比放大保持相对序
        if use_keyword_boost:
            source_boost_map = {k: v * 3.3 for k, v in source_boost_map.items()}
        fused_pks = self._rrf_fuse(
            vector_hits, bm25_hits,
            top_k=self._cfg.rerank_top_k,
            keyword_boost=use_keyword_boost,
            vector_content_map=vector_content_map,
            source_boost_map=source_boost_map,
        )
        if not fused_pks:
            return []

        fused_scores = dict(fused_pks)
        pks = [pk for pk, _ in fused_pks]
        chunks_by_pk, knowledge_by_pk = self._collect_chunks_by_pks(project_id, pks)
        return self._build_visible_contexts(
            project_id, pks, chunks_by_pk, knowledge_by_pk, actor_id, role_codes, fused_scores
        )

    async def _aretrieve(
        self,
        question: str,  # noqa: ARG002
        query_vector: list[float],
        project_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        rewrite_result,
    ) -> list[_Context]:
        # 向量检索异步并发；BM25 走线程池避免阻塞事件循环
        vector_task = asyncio.to_thread(self._collect_vector_hits, query_vector, project_id)
        bm25_task = asyncio.to_thread(self._collect_bm25_hits, rewrite_result, project_id)
        try:
            vector_hits, bm25_hits = await asyncio.gather(vector_task, bm25_task)
        except DomainError:
            raise

        use_keyword_boost = rewrite_result.query_type.value == "factual"
        vector_pks = [h.pk for h in vector_hits if h.pk]
        vector_content_map = (
            await asyncio.to_thread(self._search.get_chunks_content_by_pks, vector_pks)
            if vector_pks
            else {}
        )
        source_boost_map = await asyncio.to_thread(
            self._build_source_boost_map, vector_hits, bm25_hits
        )
        if use_keyword_boost:
            source_boost_map = {k: v * 3.3 for k, v in source_boost_map.items()}
        fused_pks = await asyncio.to_thread(
            self._rrf_fuse,
            vector_hits, bm25_hits,
            self._cfg.rerank_top_k,
            use_keyword_boost,
            vector_content_map,
            source_boost_map,
        )
        if not fused_pks:
            return []

        fused_scores = dict(fused_pks)
        pks = [pk for pk, _ in fused_pks]
        chunks_by_pk, knowledge_by_pk = await asyncio.to_thread(
            self._collect_chunks_by_pks, project_id, pks
        )
        return await asyncio.to_thread(
            self._build_visible_contexts,
            project_id, pks, chunks_by_pk, knowledge_by_pk, actor_id, role_codes, fused_scores,
        )

    # ─── RRF 融合 ──────────────────────────────────────────────────────────

    def _rrf_fuse(
        self,
        vector_hits: list[VectorSearchHit],
        bm25_hits: list[Bm25Hit],
        top_k: int | None = None,
        keyword_boost: bool = False,
        vector_content_map: dict[str, str] | None = None,
        source_boost_map: dict[str, float] | None = None,
    ) -> list[tuple[str, float]]:
        """RRF (Reciprocal Rank Fusion) 融合向量和 BM25 排名。"""
        top_k = top_k or self._cfg.rerank_top_k
        vw = self._cfg.rrf_vector_weight
        kw = self._cfg.rrf_keyword_weight
        rrf_k = self._cfg.rrf_k
        rrf: dict[str, float] = {}

        # 向量排名（cosine distance 低 = 相似度高 → 直接按 score 降序排名）
        for rank, hit in enumerate(
            sorted(
                vector_hits,
                key=lambda h: h.score if h.score is not None else 0.0,
                reverse=True,
            )
        ):
            if hit.pk:
                rrf[hit.pk] = rrf.get(hit.pk, 0.0) + vw / (rrf_k + rank + 1)

        # BM25 排名
        for rank, hit in enumerate(bm25_hits):
            if hit.chunk.id:
                pk = str(hit.chunk.id)
                rrf[pk] = rrf.get(pk, 0.0) + kw / (rrf_k + rank + 1)

        # 关键词 boost（向量召回的 content 由调用方补齐，否则只加权 BM25 命中）
        if keyword_boost and self._cfg.keyword_boost_enabled:
            chunk_content_map: dict[str, str] = {
                str(h.chunk.id): h.chunk.content for h in bm25_hits if h.chunk.id
            }
            for pk, content in (vector_content_map or {}).items():
                if pk not in chunk_content_map:
                    chunk_content_map[pk] = content
            for pk in rrf:
                boost = _keyword_score(chunk_content_map.get(pk, ""))
                if boost > 0:
                    rrf[pk] += boost

        # 来源权重：项目条款 > 企业材料 > 法规（法规措辞规整，不加权会挤占前排）
        for pk, src_boost in (source_boost_map or {}).items():
            if pk in rrf:
                rrf[pk] += src_boost

        return sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

    # ─── Embedding：sync / async ────────────────────────────────────────────

    def _embed_question(self, question: str, rewrite_result) -> list[float]:
        embed_query = rewrite_result.expanded_query
        run = self._ai_runs.start_call(
            task_id=None,
            scene="rag_query_embedding",
            model_id=EMBEDDING_MODEL_ID,
            input_payload={"question": question, "expanded_query": embed_query},
            evidence_ids=[],
        )
        started = perf_counter()
        try:
            vectors = self._embedding_client.embed([embed_query])
            if len(vectors) != 1:
                raise EmbeddingUnavailable("query embedding count is invalid")
        except EmbeddingUnavailable as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_UNAVAILABLE", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问题向量服务暂不可用", 503) from exc
        except Exception as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_FAILED", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问题向量服务暂不可用", 503) from exc
        self._ai_runs.complete_call(run, vectors, started)
        return vectors[0]

    async def _aembed_question(self, question: str, rewrite_result) -> list[float]:
        embed_query = rewrite_result.expanded_query
        run = self._ai_runs.start_call(
            task_id=None,
            scene="rag_query_embedding",
            model_id=EMBEDDING_MODEL_ID,
            input_payload={"question": question, "expanded_query": embed_query},
            evidence_ids=[],
        )
        started = perf_counter()
        try:
            if isinstance(self._embedding_client, AsyncEmbeddingClient):
                vectors = await self._embedding_client.embed([embed_query])
            else:
                vectors = await asyncio.to_thread(self._embedding_client.embed, [embed_query])
            if len(vectors) != 1:
                raise EmbeddingUnavailable("query embedding count is invalid")
        except EmbeddingUnavailable as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_UNAVAILABLE", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问题向量服务暂不可用", 503) from exc
        except Exception as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_FAILED", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问题向量服务暂不可用", 503) from exc
        self._ai_runs.complete_call(run, vectors, started)
        return vectors[0]

    # ─── Rerank：sync / async ───────────────────────────────────────────────

    def _rank(self, question: str, contexts: list[_Context], rewrite_result) -> list[_Context]:
        scores = self._call_reranker_sync(question, contexts, rewrite_result)
        cleaned = [_clean_passage_for_rerank(c.chunk.content) for c in contexts]
        return self._compose_ranked_result(contexts, cleaned, scores)

    async def _arank(
        self, question: str, contexts: list[_Context], rewrite_result
    ) -> list[_Context]:
        scores = await self._call_reranker_async(question, contexts, rewrite_result)
        cleaned = [_clean_passage_for_rerank(c.chunk.content) for c in contexts]
        return self._compose_ranked_result(contexts, cleaned, scores)

    def _start_rerank_run(
        self, question: str, contexts: list[_Context], rewrite_result
    ):
        rerank_query = rewrite_result.rerank_query
        evidence_ids = [c.evidence.id for c in contexts]
        return self._ai_runs.start_call(
            task_id=None,
            scene="rag_rerank",
            model_id=RERANKER_MODEL_ID,
            input_payload={
                "question": question,
                "rerank_query": rerank_query,
                "chunk_hashes": [c.chunk.content_hash for c in contexts],
            },
            evidence_ids=evidence_ids,
        ), rerank_query

    def _call_reranker_sync(
        self, question: str, contexts: list[_Context], rewrite_result
    ) -> list[float]:
        if not contexts:
            return []
        run, rerank_query = self._start_rerank_run(question, contexts, rewrite_result)
        cleaned = [_clean_passage_for_rerank(c.chunk.content) for c in contexts]
        valid_contents = [c for c in cleaned if c.strip()]
        started = perf_counter()
        try:
            scores = self._reranker.rerank(rerank_query, valid_contents)
            if len(scores) != len(valid_contents):
                raise RankerUnavailable("reranker result count is invalid")
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
        return scores

    async def _call_reranker_async(
        self, question: str, contexts: list[_Context], rewrite_result
    ) -> list[float]:
        if not contexts:
            return []
        run, rerank_query = self._start_rerank_run(question, contexts, rewrite_result)
        cleaned = [_clean_passage_for_rerank(c.chunk.content) for c in contexts]
        valid_contents = [c for c in cleaned if c.strip()]
        started = perf_counter()
        try:
            if isinstance(self._reranker, AsyncRankerClient):
                scores = await self._reranker.rerank(rerank_query, valid_contents)
            else:
                scores = await asyncio.to_thread(
                    self._reranker.rerank, rerank_query, valid_contents
                )
            if len(scores) != len(valid_contents):
                raise RankerUnavailable("reranker result count is invalid")
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
        return scores

    def _compose_ranked_result(
        self,
        contexts: list[_Context],
        cleaned_contents: list[str],
        scores: list[float],
    ) -> list[_Context]:
        """共享：根据 rerank 分数 + base_score 计算 Composite Score 并排序。"""
        valid_indices = [i for i, c in enumerate(cleaned_contents) if c.strip()]
        # valid_contents 与 scores 一一对应（_call_reranker_* 已校验）
        idx_map = valid_indices  # scores[i] 对应 contexts[valid_indices[i]]

        threshold = self._cfg.rerank_threshold
        filtered: list[tuple[int, float]] = [
            (i, s) for i, s in enumerate(scores) if s >= threshold
        ]

        # Threshold 退化
        if not filtered and threshold > _MIN_RERANK_THRESHOLD:
            degraded = max(threshold * _THRESHOLD_DEGRADE_RATIO, _MIN_RERANK_THRESHOLD)
            filtered = [(i, s) for i, s in enumerate(scores) if s >= degraded]
            logger.info(
                "[Rerank] threshold degraded: %.3f → %.3f", threshold, degraded
            )

        if not filtered:
            # 全部低于最低阈值：fallback 用 base_score 排序
            logger.warning(
                "[Rerank] all scores below threshold (min=%.1f), fallback to original retrieval",
                _MIN_RERANK_THRESHOLD,
            )
            valid_contexts = [contexts[idx_map[i]] for i in range(len(scores))]
            for ctx in valid_contexts:
                ctx.chunk.metadata_["composite_score"] = ctx.base_score
            ranked = sorted(
                valid_contexts, key=lambda ctx: ctx.base_score, reverse=True
            )
            return ranked

        ranked: list[tuple[float, _Context]] = []
        for i, rerank_score in filtered:
            ctx = contexts[idx_map[i]]
            composite = (
                _COMPOSITE_RERANK_W * rerank_score
                + _COMPOSITE_BASE_W * ctx.base_score
                + _COMPOSITE_SOURCE_W
            )
            ctx.chunk.metadata_["composite_score"] = composite
            ranked.append((composite, ctx))

        ranked.sort(key=lambda kv: kv[0], reverse=True)
        return [ctx for _, ctx in ranked]

    # ─── 合并流水线：步骤 2 — Content Signature 去重 ────────────────────────

    def _deduplicate_by_signature(self, contexts: list[_Context]) -> list[_Context]:
        """Content Signature 去重 + 部分重叠删除（WeKnora 第 2/9 步）。"""
        if len(contexts) <= 1:
            return contexts

        # Step 1: 按 ID 分组，保留最高 base_score
        by_id: dict[str, _Context] = {}
        for ctx in contexts:
            key = str(ctx.chunk.id)
            existing = by_id.get(key)
            if existing is None or ctx.base_score > existing.base_score:
                by_id[key] = ctx

        # Step 2: 按 Content Signature 去重，保留高分者
        candidates = sorted(by_id.values(), key=lambda c: c.base_score, reverse=True)
        by_sig: dict[str, _Context] = {}
        no_sig: list[_Context] = []
        for ctx in candidates:
            sig = _content_signature(ctx.chunk.content)
            if not sig:
                no_sig.append(ctx)
                continue
            if sig not in by_sig:
                by_sig[sig] = ctx
        deduped = list(by_sig.values()) + no_sig

        # Step 3: removePartialOverlaps
        to_remove: set[str] = set()
        n = len(deduped)
        for i in range(n):
            ai_id = str(deduped[i].chunk.id)
            if ai_id in to_remove:
                continue
            for j in range(i + 1, n):
                bj_id = str(deduped[j].chunk.id)
                if bj_id in to_remove:
                    continue
                ratio = _content_overlap_ratio(
                    deduped[i].chunk.content, deduped[j].chunk.content
                )
                if ratio >= _CONTENT_SIG_OVERLAP_THRESHOLD:
                    to_remove.add(bj_id)

        return [ctx for ctx in deduped if str(ctx.chunk.id) not in to_remove]

    # ─── 步骤 3 — 历史会话注入 ──────────────────────────────────────────────

    def _inject_history(
        self,
        current: list[_Context],
        question: str,
        session_id: str | None,
    ) -> list[_Context]:
        if not session_id or not current:
            return current

        history_msg = self._messages.get_last_assistant_message(session_id)
        if not history_msg or not history_msg.knowledge_references:
            return current

        current_ids = {str(ctx.chunk.id) for ctx in current}
        query_tokens = _tokenize_simple(question)
        if not query_tokens:
            return current

        # 从 current 拿到 project_id / actor / roles 上下文
        # 知识库 chunk 的 project_id 为 None，只能取项目文档 ctx 的上下文
        ref_ctx = next((c for c in current if c.chunk.project_id is not None), None)
        if ref_ctx is None:
            return current
        project_id = ref_ctx.chunk.project_id
        actor_id = ref_ctx.evidence.actor_id
        role_codes = ref_ctx.evidence.role_codes

        injected: list[_Context] = []
        for ref in history_msg.knowledge_references:
            if len(injected) >= _HISTORY_MAX_INJECT:
                break
            ref_id = ref.get("chunk_id") or ref.get("id")
            if not ref_id or ref_id in current_ids:
                continue
            content = ref.get("content") or ref.get("text", "")
            if not content:
                continue
            sim = _jaccard_set(query_tokens, _tokenize_simple(content))
            if sim < _HISTORY_MIN_SIMILARITY:
                continue

            try:
                chunk_uuid = UUID(str(ref_id))
            except (ValueError, AttributeError, TypeError):
                continue

            chunk = self._search.get_chunk(chunk_uuid)
            if chunk is None or chunk.evidence_id is None:
                continue
            try:
                evidence = self._evidences.get_visible_for_project(
                    chunk.evidence_id, project_id, actor_id, role_codes
                )
            except DomainError:
                continue

            injected.append(
                _Context(
                    chunk=chunk,
                    evidence=evidence,
                    base_score=(ref.get("score") or 1.0) * _HISTORY_SCORE_DISCOUNT,
                )
            )

        if not injected:
            return current
        return current + injected

    # ─── 步骤 4 — 父子块展开 ───────────────────────────────────────────────

    def _resolve_parent_chunks(self, contexts: list[_Context]) -> list[_Context]:
        """父子块解析（WeKnora MatchTypeParentChunk 语义）。

        - 同父多子命中：合并为一条 context，父块内容只出现一次（避免 prompt 重复）
        - 独子命中：父块内容 + 子块内容拼接（父块在前提供完整上下文）
        保持输入的排序顺序不变。
        """
        if not contexts:
            return contexts

        parent_ids = {c.chunk.parent_chunk_id for c in contexts if c.chunk.parent_chunk_id}
        if not parent_ids:
            return contexts

        parent_map: dict[UUID, SearchChunk] = {}
        for pid in parent_ids:
            parent = self._search.get_chunk(pid)
            if parent is not None:
                parent_map[pid] = parent

        result: list[_Context] = []
        # parent_id -> (载体 ctx, 同父兄弟 ctx 列表)
        carriers: dict[UUID, tuple[_Context, list[_Context]]] = {}
        for ctx in contexts:
            pid = ctx.chunk.parent_chunk_id
            if pid and pid in parent_map:
                if pid in carriers:
                    carriers[pid][1].append(ctx)
                    continue
                carriers[pid] = (ctx, [])
            result.append(ctx)

        for pid, (carrier, siblings) in carriers.items():
            parent = parent_map[pid]
            if siblings:
                # 同父多子：只保留父块全文一次，记录被合并的子块锚点
                carrier.chunk.content = parent.content
                carrier.chunk.metadata_["merged_child_ids"] = [
                    str(carrier.chunk.id),
                    *(str(s.chunk.id) for s in siblings),
                ]
            else:
                carrier.chunk.content = f"{parent.content}\n{carrier.chunk.content}"

        return result

    # ─── 步骤 5 / 7.5 — Sequential Merge ────────────────────────────────────

    def _merge_sequential(self, contexts: list[_Context]) -> list[_Context]:
        """按 StartAt/EndAt 相邻位置合并 sequential chunks（WeKnora 第 5/7.5 步）。"""
        if len(contexts) <= 1:
            return contexts

        sorted_contexts = sorted(contexts, key=lambda c: c.chunk.chunk_index)
        chunks = [ctx.chunk for ctx in sorted_contexts]
        merged = merge_sequential_chunks(chunks)

        # merged 中每个 chunk 携带 SubChunkIDs 标记被吸收的 chunk
        # 主 chunk 的 content/end_at 已是合并后状态
        primary_by_sub: dict[str, SearchChunk] = {}
        for m in merged:
            primary_by_sub[str(m.id)] = m
            for sub_id in (m.metadata_.get("sub_chunk_ids") or []):
                primary_by_sub[str(sub_id)] = m

        result: list[_Context] = []
        consumed: set[str] = set()
        for ctx in sorted_contexts:
            cid = str(ctx.chunk.id)
            if cid in consumed:
                continue
            primary = primary_by_sub.get(cid)
            if primary is not None and primary.id != ctx.chunk.id:
                # 此 chunk 被合并到 primary，content 已由 merge_sequential_chunks 写入 primary
                # 跳过本 chunk（内容已在 primary 的 context 中体现）
                consumed.add(cid)
                # 将 primary 的内容应用到对应的 context（找到持有 primary.id 的 ctx）
                continue
            if primary is not None:
                ctx.chunk.content = primary.content
                ctx.chunk.end_at = primary.end_at
                # 标记被吸收的 id
                for sub_id in (primary.metadata_.get("sub_chunk_ids") or []):
                    consumed.add(str(sub_id))
            result.append(ctx)
        return result

    # ─── 步骤 7 — 邻居扩展 ──────────────────────────────────────────────────

    def _expand_neighbors(self, contexts: list[_Context]) -> list[_Context]:
        if len(contexts) <= 1:
            return contexts

        # 批量加载涉及版本的全部子块（按 chunk_index 有序），内存中完成扩展，
        # 避免逐 hop 查库（旧实现每个 context 十几次 DB 往返）
        version_ids = {
            c.chunk.source_document_version_id
            for c in contexts
            if len(c.chunk.content) < _NEIGHBOR_MIN_CHARS
            and c.chunk.source_document_version_id is not None
        }
        chains: dict[UUID, list[SearchChunk]] = {}
        positions: dict[UUID, dict[UUID, int]] = {}
        for vid in version_ids:
            ordered = [
                c
                for c in self._search.list_chunks_for_version(vid)
                if c.content_type == "text"
            ]
            chains[vid] = ordered
            positions[vid] = {c.id: i for i, c in enumerate(ordered)}

        result: list[_Context] = []
        for ctx in contexts:
            content = ctx.chunk.content
            if len(content) >= _NEIGHBOR_MIN_CHARS:
                result.append(ctx)
                continue

            vid = ctx.chunk.source_document_version_id
            pos = positions.get(vid, {}).get(ctx.chunk.id)
            if pos is None:
                result.append(ctx)
                continue
            chain = chains[vid]

            expanded = content
            i = pos - 1
            # 向前扩展
            while i >= 0 and len(expanded) + len(chain[i].content) + 1 <= _NEIGHBOR_MAX_CHARS:
                expanded = chain[i].content + "\n" + expanded
                i -= 1
            # 向后扩展
            j = pos + 1
            while (
                j < len(chain)
                and len(expanded) + len(chain[j].content) + 1 <= _NEIGHBOR_MAX_CHARS
            ):
                expanded = expanded + "\n" + chain[j].content
                j += 1

            if expanded != content:
                ctx.chunk.content = expanded
            result.append(ctx)
        return result

    # ─── 步骤 8 — MMR 去重 ─────────────────────────────────────────────────

    def _mmr_diversify(
        self, contexts: list[_Context], lambda_: float = _MMR_LAMBDA
    ) -> list[_Context]:
        if len(contexts) <= 2:
            return contexts

        all_tokens = [_tokenize_jieba(c.chunk.content) for c in contexts]
        # base relevance：优先 composite_score，缺失时退化为 base_score
        relevances = [
            c.chunk.metadata_.get("composite_score", c.base_score) for c in contexts
        ]

        selected: list[_Context] = []
        selected_token_sets: list[frozenset[str]] = []
        selected_indices: set[int] = set()
        n = len(contexts)

        while len(selected) < n:
            best_idx = -1
            best_score = float("-inf")
            for i, candidate in enumerate(contexts):
                if i in selected_indices:
                    continue
                redundancy = 0.0
                for sel_tokens in selected_token_sets:
                    sim = _jaccard_set(all_tokens[i], sel_tokens)
                    if sim > redundancy:
                        redundancy = sim
                mmr_score = lambda_ * relevances[i] - (1.0 - lambda_) * redundancy
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = i
            if best_idx < 0:
                break
            selected.append(contexts[best_idx])
            selected_token_sets.append(all_tokens[best_idx])
            selected_indices.add(best_idx)
            # 当剩余候选 MMR 都非正且无新增价值时提前终止
            if best_score <= 0 and len(selected) >= max(2, n // 2):
                break

        return selected

    # ─── 准备检索（供外部 sync / async 调用） ────────────────────────────────

    def _prepare_retrieval(
        self, project_id: UUID, actor_id: UUID, role_codes: set[str], question: str
    ) -> tuple[list[dict[str, Any]], list[float]]:
        self._projects.get_visible(project_id, actor_id, role_codes)
        if not self._settings.ai_is_configured:
            raise DomainError(
                "AI_SERVICE_UNAVAILABLE", "问答 AI 服务尚未配置或暂不可用", 503
            )

        rewrite_result = rewrite_query(question)
        query_vector = self._embed_question(question, rewrite_result)
        contexts = self._retrieve(
            question, query_vector, project_id, actor_id, role_codes, rewrite_result
        )
        logger.info(
            "[RAG] _prepare_retrieval: project=%s question=%r contexts_count=%d",
            project_id, question[:30], len(contexts),
        )
        if not contexts:
            return [], query_vector

        ranked = self._rank(question, contexts, rewrite_result)
        # 外部调用与 answer() 走同一套 8 步合并管线
        ranked = self._deduplicate_by_signature(ranked)
        ranked = self._resolve_parent_chunks(ranked)
        ranked = self._merge_sequential(ranked)
        ranked = self._expand_neighbors(ranked)
        ranked = self._merge_sequential(ranked)
        ranked = self._mmr_diversify(ranked)
        ranked = self._truncate_by_budget(ranked)
        logger.info("[RAG] _prepare_retrieval: ranked_count=%d", len(ranked))
        return (
            [
                {"evidence_id": str(item.evidence.id), "content": item.chunk.content}
                for item in ranked[:RAG_CONTEXT_LIMIT]
            ],
            query_vector,
        )

    async def _aprepare_retrieval(
        self,
        project_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        question: str,
        session_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[float]]:
        self._projects.get_visible(project_id, actor_id, role_codes)
        if not self._settings.ai_is_configured:
            raise DomainError(
                "AI_SERVICE_UNAVAILABLE", "问答 AI 服务尚未配置或暂不可用", 503
            )

        rewrite_result = rewrite_query(question)
        query_vector = await self._aembed_question(question, rewrite_result)
        contexts = await self._aretrieve(
            question, query_vector, project_id, actor_id, role_codes, rewrite_result
        )
        logger.info(
            "[RAG] _aprepare_retrieval: project=%s question=%r contexts_count=%d",
            project_id, question[:30], len(contexts),
        )
        if not contexts:
            return [], query_vector

        ranked = await self._arank(question, contexts, rewrite_result)
        # 外部调用与 answer() 走同一套 8 步合并管线（同步线程池，避免阻塞事件循环）
        ranked = await asyncio.to_thread(self._deduplicate_by_signature, ranked)
        ranked = await asyncio.to_thread(self._inject_history, ranked, question, session_id)
        ranked = await asyncio.to_thread(self._resolve_parent_chunks, ranked)
        ranked = await asyncio.to_thread(self._merge_sequential, ranked)
        ranked = await asyncio.to_thread(self._expand_neighbors, ranked)
        ranked = await asyncio.to_thread(self._merge_sequential, ranked)
        ranked = await asyncio.to_thread(self._mmr_diversify, ranked)
        ranked = await asyncio.to_thread(self._truncate_by_budget, ranked)
        logger.info("[RAG] _aprepare_retrieval: ranked_count=%d", len(ranked))
        return (
            [
                {"evidence_id": str(item.evidence.id), "content": item.chunk.content}
                for item in ranked[:RAG_CONTEXT_LIMIT]
            ],
            query_vector,
        )

    # ─── 生成回答 + 引文校验 ────────────────────────────────────────────────

    @staticmethod
    def _truncate_by_budget(contexts: list[_Context]) -> list[_Context]:
        """按 composite_score 降序累计，超 _ANSWER_PROMPT_MAX_CHARS 时截断。

        至少保留 1 条（即使超长）以保证答案能生成；其余按分数高到低贪心填入。
        """
        if not contexts:
            return contexts
        # composite_score 已在 _compose_ranked_result 中写入 metadata
        scored = sorted(
            contexts,
            key=lambda c: c.chunk.metadata_.get("composite_score", c.base_score),
            reverse=True,
        )
        kept: list[_Context] = []
        total = 0
        budget = _ANSWER_PROMPT_MAX_CHARS
        for ctx in scored:
            cost = len(ctx.chunk.content)
            if kept and total + cost > budget:
                continue
            kept.append(ctx)
            total += cost
        return kept

    def _answer_with_citations(
        self, question: str, contexts: list[_Context]
    ) -> RagAnswerResponse:
        # Token 预算截断：按 rerank composite_score 从高到低保留，
        # 直到累加正文长度逼近 _ANSWER_PROMPT_MAX_CHARS（WeKnora 风格）。
        # 这里按 rune 粗估（中文 1 字 ≈ 1 rune ≈ 1.5 token），给 prompt 留安全裕度。
        truncated = self._truncate_by_budget(contexts)
        if len(truncated) < len(contexts):
            logger.info(
                "[RAG] answer contexts truncated: %d -> %d (budget %d chars)",
                len(contexts), len(truncated), _ANSWER_PROMPT_MAX_CHARS,
            )
        contexts = truncated
        evidence_ids = [c.evidence.id for c in contexts]
        run = self._ai_runs.start_call(
            task_id=None,
            scene="rag_answer",
            model_id=LLM_MODEL_ID,
            input_payload={
                "question": question,
                "chunk_hashes": [c.chunk.content_hash for c in contexts],
            },
            evidence_ids=evidence_ids,
        )
        started = perf_counter()
        try:
            draft = self._llm.answer_question(
                question,
                [
                    {"evidence_id": str(c.evidence.id), "content": c.chunk.content}
                    for c in contexts
                ],
            )
        except LlmRateLimited as exc:
            self._ai_runs.fail_call(run, "AI_RATE_LIMITED", started)
            raise DomainError("AI_RATE_LIMITED", "问答服务繁忙，请稍后重试", 429) from exc
        except LlmAuthenticationFailed as exc:
            self._ai_runs.fail_call(run, "AI_AUTH_FAILED", started)
            logger.exception("[RAG] answer LLM auth failed")
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问答生成服务暂不可用", 503) from exc
        except LlmTimeout as exc:
            self._ai_runs.fail_call(run, "AI_TIMEOUT", started)
            raise DomainError("AI_TIMEOUT", "问答生成超时，请重试", 504) from exc
        except LlmUnavailable as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_UNAVAILABLE", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问答生成服务暂不可用", 503) from exc
        except Exception as exc:
            self._ai_runs.fail_call(run, "AI_SERVICE_FAILED", started)
            raise DomainError("AI_SERVICE_UNAVAILABLE", "问答生成服务暂不可用", 503) from exc

        allowed = {c.evidence.id for c in contexts}
        cited = list(dict.fromkeys(draft.evidence_ids))
        if any(eid not in allowed for eid in cited):
            self._ai_runs.invalidate_call(run, "INVALID_EVIDENCE_CITATION", started)
            return self._no_evidence()

        self._ai_runs.complete_call(run, draft.model_dump(mode="json"), started)
        if not cited:
            return self._no_evidence()

        context_by_evidence = {c.evidence.id: c for c in contexts}
        return RagAnswerResponse(
            answer=draft.answer,
            citations=[
                self._citation(context_by_evidence[eid].evidence) for eid in cited
            ],
            no_evidence=False,
        )

    @staticmethod
    def _citation(evidence: EvidenceResponse) -> RagCitation:
        return RagCitation(
            evidence_id=evidence.id,
            document_id=evidence.document_id,
            document_version_id=evidence.document_version_id,
            file_name=evidence.file_name,
            version_no=evidence.version_no,
            page_number=evidence.page_number,
            quoted_text=evidence.quoted_text,
        )

    @staticmethod
    def _no_evidence() -> RagAnswerResponse:
        return RagAnswerResponse(answer="未找到证据", citations=[], no_evidence=True)
