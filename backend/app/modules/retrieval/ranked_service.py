"""项目 Evidence 的统一最终排序层。"""

from uuid import UUID

import jieba
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.reranker import RankV2Reranker, RerankerUnavailable
from app.modules.evidence.repository import EvidenceRepository
from app.modules.identity.service import AuthenticatedUser
from app.modules.retrieval.candidate_service import EvidenceCandidateRetrievalService
from app.modules.retrieval.schemas import EvidenceSearchHit


class RankedEvidenceRetrievalService:
    """候选召回 -> rerank -> MMR -> 最终候选邻居扩展。"""

    def __init__(
        self,
        session: AsyncSession,
        candidates: EvidenceCandidateRetrievalService,
        reranker: RankV2Reranker,
    ) -> None:
        self._session = session
        self._candidates = candidates
        self._reranker = reranker
        self._evidences = EvidenceRepository(session)

    async def retrieve(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
        question: str,
        *,
        limit: int = 8,
    ) -> list[EvidenceSearchHit]:
        response = await self._candidates.search_project_evidences(
            project_id, actor, question, max(20, limit * 3)
        )
        candidates = response.items
        if not candidates:
            return []

        try:
            scores = await self._reranker.rerank(
                question,
                [self._rerank_text(item) for item in candidates],
            )
            ranked = sorted(
                zip(candidates, scores, strict=True), key=lambda row: row[1], reverse=True
            )
        except RerankerUnavailable:
            ranked = [(item, item.similarity) for item in candidates]

        selected = self._mmr_diversify(ranked, limit)
        chain_cache: dict[UUID, list[object]] = {}
        output: list[EvidenceSearchHit] = []
        for item in selected:
            context = await self._expand_neighbors(project_id, item, chain_cache)
            section_path = str((item.locator or {}).get("section_path") or "").strip()
            output.append(
                item.model_copy(
                    update={
                        "context_text": context,
                        "parent_context": section_path or item.parent_context,
                    }
                )
            )
        return output

    async def _expand_neighbors(
        self,
        project_id: UUID,
        hit: EvidenceSearchHit,
        chain_cache: dict[UUID, list[object]],
    ) -> str:
        text = self._retrieval_text(hit.quoted_text, hit.locator)
        if len(text) >= 600:
            return text
        evidence = await self._evidences.get_in_project(project_id, hit.evidence_id)
        if evidence is None or evidence.document_version_id is None:
            return text
        section_path = str((evidence.locator or {}).get("section_path") or "")
        chain = chain_cache.get(evidence.document_version_id)
        if chain is None:
            chain = await self._evidences.list_for_document_version(
                project_id, evidence.document_version_id
            )
            chain_cache[evidence.document_version_id] = chain
        try:
            index = next(i for i, item in enumerate(chain) if item.id == evidence.id)
        except StopIteration:
            return text

        parts = [text]
        total = len(text)
        for direction in (-1, 1):
            cursor = index + direction
            while 0 <= cursor < len(chain):
                neighbor = chain[cursor]
                neighbor_section = str((neighbor.locator or {}).get("section_path") or "")
                neighbor_text = self._retrieval_text(neighbor.quoted_text, neighbor.locator)
                if neighbor_section != section_path or not neighbor_text:
                    break
                if total + len(neighbor_text) + 1 > 2_000:
                    break
                if direction < 0:
                    parts.insert(0, neighbor_text)
                else:
                    parts.append(neighbor_text)
                total += len(neighbor_text) + 1
                cursor += direction
        return "\n".join(parts)

    @staticmethod
    def _rerank_text(item: EvidenceSearchHit) -> str:
        locator = item.locator or {}
        suffix = "\n".join(
            part
            for part in (
                f"文档：{locator.get('document_name')}" if locator.get("document_name") else "",
                f"章节：{locator.get('section_path')}" if locator.get("section_path") else "",
            )
            if part
        )
        text = RankedEvidenceRetrievalService._retrieval_text(item.quoted_text, locator)
        return f"{text}\n{suffix}".strip()

    @staticmethod
    def _retrieval_text(quoted_text: str | None, locator: object) -> str:
        metadata = locator if isinstance(locator, dict) else {}
        return str(metadata.get("retrieval_text") or quoted_text or "").strip()

    @staticmethod
    def _mmr_diversify(
        ranked: list[tuple[EvidenceSearchHit, float]], limit: int
    ) -> list[EvidenceSearchHit]:
        if not ranked or limit <= 0:
            return []
        values = [float(score) for _, score in ranked]
        high = max(values)
        if high > 0:
            # 按峰值归一到 [0,1]，保留 rerank 概率的绝对差距；
            # min-max 拉伸会把最低分压成 0，导致近重复项永远压过差异化命中。
            normalized = [(item, float(score) / high) for item, score in ranked]
        else:
            size = max(len(ranked), 1)
            normalized = [
                (item, 1.0 - (index / size)) for index, (item, _score) in enumerate(ranked)
            ]

        selected: list[EvidenceSearchHit] = []
        selected_tokens: list[frozenset[str]] = []
        remaining = normalized[:]
        while remaining and len(selected) < limit:
            best_index = 0
            best_score = float("-inf")
            for index, (candidate, relevance) in enumerate(remaining):
                tokens = _tokens(candidate.quoted_text)
                redundancy = max(
                    (_jaccard(tokens, prior) for prior in selected_tokens), default=0.0
                )
                score = 0.8 * relevance - 0.2 * redundancy
                if score > best_score:
                    best_index, best_score = index, score
            candidate, _ = remaining.pop(best_index)
            selected.append(candidate)
            selected_tokens.append(_tokens(candidate.quoted_text))
        return selected


def _tokens(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    for token in jieba.cut_for_search(text.lower()):
        normalized = token.strip()
        if len(normalized) > 1 and any(char.isalnum() for char in normalized):
            tokens.add(normalized)
    return frozenset(tokens)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
