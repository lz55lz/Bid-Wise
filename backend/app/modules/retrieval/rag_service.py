"""项目范围的确定性 RAG 回答编排。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.integrations.embedding import BgeM3EmbeddingClient
from app.integrations.llm import LlmUnavailable, MiniMaxM3Client
from app.integrations.reranker import RankV2Reranker
from app.modules.identity.service import AuthenticatedUser
from app.modules.retrieval.ranked_service import RankedEvidenceRetrievalService
from app.modules.retrieval.schemas import RagAnswerResponse, RagCitation
from app.modules.retrieval.candidate_service import EvidenceCandidateRetrievalService


class ProjectRagService:
    """固定流程：候选召回、统一排序、受控回答；LLM 不允许绕过检索直答。"""

    def __init__(
        self,
        session: AsyncSession,
        embedding_client: BgeM3EmbeddingClient,
        reranker: RankV2Reranker,
        llm: MiniMaxM3Client,
    ) -> None:
        candidates = EvidenceCandidateRetrievalService(session, embedding_client)
        self._retrieval = RankedEvidenceRetrievalService(session, candidates, reranker)
        self._llm = llm

    @staticmethod
    def bounded_contexts(items, *, budget: int = 10_000) -> list[dict[str, str]]:
        """按共享字符预算装载最终 TopK；引用白名单只来自真正进入上下文的条目。"""
        contexts: list[dict[str, str]] = []
        remaining = budget
        for item in items:
            if remaining <= 0:
                break
            # 兼容只带 quoted_text 的轻量命中对象，避免缺失 context_text 时整个问答抛错。
            evidence_context = (
                getattr(item, "context_text", None) or item.quoted_text or ""
            )
            content = (
                f"章节：{item.parent_context}\n\n命中内容：{evidence_context}"
                if item.parent_context
                else evidence_context
            )
            clipped = content[: min(2_000, remaining)]
            if clipped.strip():
                contexts.append({"evidence_id": str(item.evidence_id), "content": clipped})
                remaining -= len(clipped)
        return contexts

    async def answer(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
        question: str,
    ) -> RagAnswerResponse:
        ranked = await self.retrieve(project_id, actor, question, limit=6)
        if not ranked:
            return RagAnswerResponse(answer="未找到证据", citations=[], no_evidence=True)
        contexts = self.bounded_contexts(ranked)
        if not contexts:
            return RagAnswerResponse(answer="未找到证据", citations=[], no_evidence=True)
        try:
            answer, selected_ids = await self._llm.answer_from_evidence(question, contexts)
        except LlmUnavailable as exc:
            raise DomainError("LLM_UNAVAILABLE", "问答模型暂不可用", 503) from exc

        # 只允许引用真正发给 LLM 的 Evidence，避免预算截断后仍保留额外引用白名单。
        allowed_ids = {UUID(item["evidence_id"]) for item in contexts}
        ranked_by_id = {item.evidence_id: item for item in ranked if item.evidence_id in allowed_ids}
        citations = [
            RagCitation(
                evidence_id=evidence_id,
                quoted_text=ranked_by_id[evidence_id].quoted_text,
                locator=ranked_by_id[evidence_id].locator,
            )
            for evidence_id in selected_ids
            if evidence_id in ranked_by_id
        ]
        if not citations:
            return RagAnswerResponse(answer="未找到证据", citations=[], no_evidence=True)
        return RagAnswerResponse(answer=answer, citations=citations, no_evidence=False)

    async def retrieve(
        self, project_id: UUID, actor: AuthenticatedUser, question: str, *, limit: int = 8
    ):
        return await self._retrieval.retrieve(project_id, actor, question, limit=limit)

