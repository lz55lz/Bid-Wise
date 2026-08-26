"""Clause-level multi-route candidate recall before Requirement LLM extraction.

Search chunks are retrieval locators only.  The only text that can be sent to
the Requirement LLM is the complete, evidence-linked ``TenderClause``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SearchChunk, TenderClause
from app.db.repositories.clause_repository import ClauseRepository
from app.db.repositories.document_repository import DocumentRepository
from app.integrations.ai.embedding import EmbeddingClient, EmbeddingUnavailable
from app.integrations.vector_store import VectorStore
from app.services.keyword_scoring_service import KeywordScoringService
from app.services.node_label_policy import NodeLabelPolicy

logger = logging.getLogger(__name__)

_POLICY_VERSION = "clause-candidate-recall/v1"
_RRF_K = 60
_LLM_BUDGET = 24
_OVERFETCH_LIMIT = _LLM_BUDGET * 5
_ALLOWED_SCOPES = frozenset({"BIDDER_REQUIREMENT", "SCORING_CRITERIA"})
_ROUTE_WEIGHTS = {"policy": 0.50, "keyword": 0.20, "hybrid": 0.30}

# Focused intents are deliberately small and stable.  They retrieve potential
# tender clauses, never generate a business conclusion.
_HYBRID_PROFILES = {
    "qualification": "投标人资格 资质 业绩 人员 证书 联合体",
    "submission": "投标文件 递交 截止 开标 签章 密封 保证金",
    "business": "报价 工期 交货 付款 履约 验收 合同 质保",
    "scoring": "评标 评分 评审 分值 技术参数 方案",
}


@dataclass(frozen=True, slots=True)
class ClauseCandidateRecallSummary:
    total_clauses: int
    eligible_clauses: int
    rule_direct_clauses: int
    llm_selected_clauses: int
    deferred_clauses: int
    hybrid_status: str


class ClauseCandidateRecallService:
    """Fuse deterministic and hybrid recall routes at complete-clause level."""

    def __init__(
        self,
        session: Session,
        *,
        embedding_client: EmbeddingClient | None = None,
        vector_store: VectorStore | None = None,
        label_policy: NodeLabelPolicy | None = None,
        llm_budget: int = _LLM_BUDGET,
    ) -> None:
        if llm_budget <= 0:
            raise ValueError("llm_budget must be positive")
        self._session = session
        self._clauses = ClauseRepository(session)
        self._documents = DocumentRepository(session)
        self._embedding_client = embedding_client
        self._vector_store = vector_store
        self._label_policy = label_policy or NodeLabelPolicy.from_session(session)
        self._keyword_scorer = KeywordScoringService()
        self._llm_budget = llm_budget

    def select_for_extraction(self, document_version_id: UUID) -> ClauseCandidateRecallSummary:
        """Persist one auditable selection decision for every TenderClause."""
        clauses = self._clauses.list_for_version(document_version_id)
        if not clauses:
            return ClauseCandidateRecallSummary(0, 0, 0, 0, 0, "NO_CLAUSES")

        version = self._documents.get_version(document_version_id)
        document = None if version is None else self._documents.get_document(version.document_id)
        if version is None or document is None or document.project_id is None:
            raise ValueError("invalid tender document")

        records = self._build_records(clauses)
        self._rank_policy_route(records)
        self._rank_keyword_route(records)
        hybrid_ranks, hybrid_status = self._hybrid_ranks_by_source_node(
            document_version_id, document.project_id, records
        )
        self._apply_hybrid_ranks(records, hybrid_ranks)
        self._fuse_rrf(records)
        selected_ids = self._select_diverse_llm_candidates(records)
        summary = self._persist_decisions(records, selected_ids, hybrid_status)
        version_summary = dict(version.cleaning_summary or {})
        version_summary["clause_candidate_recall"] = {
            "policy_version": _POLICY_VERSION,
            "total_clauses": summary.total_clauses,
            "eligible_clauses": summary.eligible_clauses,
            "rule_direct_clauses": summary.rule_direct_clauses,
            "llm_selected_clauses": summary.llm_selected_clauses,
            "deferred_clauses": summary.deferred_clauses,
            "hybrid_status": summary.hybrid_status,
        }
        version.cleaning_summary = version_summary
        self._session.commit()
        logger.info(
            "[ClauseRecall] version=%s clauses=%d eligible=%d rule=%d llm=%d deferred=%d hybrid=%s",
            document_version_id,
            summary.total_clauses,
            summary.eligible_clauses,
            summary.rule_direct_clauses,
            summary.llm_selected_clauses,
            summary.deferred_clauses,
            summary.hybrid_status,
        )
        return summary

    def _build_records(self, clauses: Sequence[TenderClause]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for clause in clauses:
            content = clause.content.strip()
            labels = self._label_policy.label({
                "chunk_text": content,
                "section_path": clause.section_path or "",
                "chunk_type": "clause",
            })
            keyword_score, keyword_matches = self._keyword_scorer.score_node(
                content, clause.section_path or ""
            )
            eligible = self._is_eligible(labels, keyword_score)
            labels["requirement_candidate"] = eligible
            records.append({
                "clause": clause,
                "labels": labels,
                "keyword_score": keyword_score,
                "keyword_matches": keyword_matches,
                "eligible": eligible,
                "routes": {},
                "rrf_score": 0.0,
            })
        return records

    @staticmethod
    def _is_eligible(labels: dict[str, Any], keyword_score: int) -> bool:
        if labels.get("noise") or labels.get("analysis_scope") not in _ALLOWED_SCOPES:
            return False
        if not labels.get("domains"):
            return False
        return bool(
            labels.get("mandatory_signal")
            or labels.get("quantitative_signal")
            or labels.get("matched_tag_codes")
            or keyword_score > 0
        )

    @staticmethod
    def _policy_priority(record: dict[str, Any]) -> tuple[int, int, int, int, int]:
        labels = record["labels"]
        return (
            int(bool(labels.get("blocking_signal"))),
            int(bool(labels.get("mandatory_signal"))),
            int(bool(labels.get("matched_tag_codes"))),
            int(bool(labels.get("quantitative_signal"))),
            -record["clause"].order_no,
        )

    def _rank_policy_route(self, records: list[dict[str, Any]]) -> None:
        candidates = sorted(
            (record for record in records if record["eligible"]),
            key=self._policy_priority,
            reverse=True,
        )
        for rank, record in enumerate(candidates, start=1):
            record["routes"]["policy"] = {"rank": rank}

    def _rank_keyword_route(self, records: list[dict[str, Any]]) -> None:
        candidates = sorted(
            (
                record
                for record in records
                if record["eligible"] and record["keyword_score"] > 0
            ),
            key=lambda record: (record["keyword_score"], -record["clause"].order_no),
            reverse=True,
        )
        for rank, record in enumerate(candidates, start=1):
            record["routes"]["keyword"] = {
                "rank": rank,
                "score": record["keyword_score"],
                "matched": record["keyword_matches"],
            }

    def _hybrid_ranks_by_source_node(
        self,
        document_version_id: UUID,
        project_id: UUID,
        records: Sequence[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, int]], str]:
        if self._embedding_client is None or self._vector_store is None:
            return {}, "DISABLED"
        source_node_ids = {
            source_node_id
            for record in records
            for source_node_id in self._source_node_ids(record["clause"])
        }
        if not source_node_ids:
            return {}, "NO_SOURCE_NODES"
        try:
            vectors = self._embedding_client.embed(list(_HYBRID_PROFILES.values()))
            if len(vectors) != len(_HYBRID_PROFILES):
                raise EmbeddingUnavailable("embedding result count does not match recall profiles")
            hits_by_profile: dict[str, list[str]] = {}
            for (profile, query), vector in zip(_HYBRID_PROFILES.items(), vectors, strict=True):
                hits = self._vector_store.search_hybrid_tender_version(
                    vector,
                    query,
                    str(project_id),
                    str(document_version_id),
                    _OVERFETCH_LIMIT,
                )
                hits_by_profile[profile] = [hit.pk for hit in hits]
        except Exception as exc:
            logger.info("[ClauseRecall] hybrid route unavailable: %s", exc)
            return {}, "UNAVAILABLE"

        chunk_ids = [chunk_id for hits in hits_by_profile.values() for chunk_id in hits]
        rows = self._session.execute(
            select(SearchChunk.id, SearchChunk.source_node_id).where(
                SearchChunk.id.in_(chunk_ids),
                SearchChunk.source_document_version_id == document_version_id,
                SearchChunk.deleted_at.is_(None),
            )
        ).tuples()
        source_by_chunk = {
            str(chunk_id): str(source_node_id)
            for chunk_id, source_node_id in rows
            if source_node_id
        }
        ranks: dict[str, dict[str, int]] = defaultdict(dict)
        for profile, chunk_ids_for_profile in hits_by_profile.items():
            seen_nodes: set[str] = set()
            rank = 0
            for chunk_id in chunk_ids_for_profile:
                source_node_id = source_by_chunk.get(chunk_id)
                if source_node_id is None or source_node_id in seen_nodes:
                    continue
                seen_nodes.add(source_node_id)
                rank += 1
                ranks[source_node_id][profile] = rank
        return dict(ranks), "AVAILABLE"

    @staticmethod
    def _source_node_ids(clause: TenderClause) -> list[str]:
        metadata = clause.quality_metadata or {}
        raw_ids = metadata.get("source_node_ids") or []
        return [str(value) for value in raw_ids if value]

    def _apply_hybrid_ranks(
        self,
        records: Iterable[dict[str, Any]],
        ranks_by_node: dict[str, dict[str, int]],
    ) -> None:
        for record in records:
            profile_ranks: dict[str, int] = {}
            for source_node_id in self._source_node_ids(record["clause"]):
                for profile, rank in ranks_by_node.get(source_node_id, {}).items():
                    previous = profile_ranks.get(profile)
                    if previous is None or rank < previous:
                        profile_ranks[profile] = rank
            if profile_ranks:
                record["routes"]["hybrid"] = {"profiles": profile_ranks}

    @staticmethod
    def _rrf(rank: int, weight: float) -> float:
        return weight / (_RRF_K + rank)

    def _fuse_rrf(self, records: Iterable[dict[str, Any]]) -> None:
        hybrid_profile_weight = _ROUTE_WEIGHTS["hybrid"] / len(_HYBRID_PROFILES)
        for record in records:
            score = 0.0
            policy = record["routes"].get("policy")
            if policy:
                score += self._rrf(policy["rank"], _ROUTE_WEIGHTS["policy"])
            keyword = record["routes"].get("keyword")
            if keyword:
                score += self._rrf(keyword["rank"], _ROUTE_WEIGHTS["keyword"])
            hybrid = record["routes"].get("hybrid", {})
            for rank in hybrid.get("profiles", {}).values():
                score += self._rrf(rank, hybrid_profile_weight)
            record["rrf_score"] = score

    def _select_diverse_llm_candidates(self, records: Sequence[dict[str, Any]]) -> set[UUID]:
        candidates = [
            record for record in records
            if record["eligible"] and not self._is_rule_direct(record["labels"])
        ]
        candidates.sort(
            key=lambda record: (record["rrf_score"], -record["clause"].order_no),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        selected_ids: set[UUID] = set()
        domains = sorted({
            domain for record in candidates for domain in record["labels"].get("domains", [])
        })
        # One round per domain prevents a dense payment/guarantee section from
        # consuming the whole LLM context.  Remaining slots follow global RRF.
        for domain in domains:
            candidate = next(
                (
                    record for record in candidates
                    if domain in record["labels"].get("domains", [])
                    and record["clause"].id not in selected_ids
                ),
                None,
            )
            if candidate is not None and len(selected) < self._llm_budget:
                selected.append(candidate)
                selected_ids.add(candidate["clause"].id)
        for candidate in candidates:
            if len(selected) >= self._llm_budget:
                break
            if candidate["clause"].id not in selected_ids:
                selected.append(candidate)
                selected_ids.add(candidate["clause"].id)
        for rank, record in enumerate(selected, start=1):
            record["llm_rank"] = rank
        return selected_ids

    @staticmethod
    def _is_rule_direct(labels: dict[str, Any]) -> bool:
        domains = {str(domain) for domain in labels.get("domains", []) if domain}
        return bool(
            (
                labels.get("blocking_signal")
                and labels.get("analysis_scope") != "SCORING_CRITERIA"
            )
            or (
                labels.get("analysis_scope") != "SCORING_CRITERIA"
                and labels.get("mandatory_signal")
                and len(domains) == 1
            )
        )

    def _persist_decisions(
        self,
        records: Iterable[dict[str, Any]],
        selected_ids: set[UUID],
        hybrid_status: str,
    ) -> ClauseCandidateRecallSummary:
        total = eligible = rule_direct = llm_selected = deferred = 0
        for record in records:
            total += 1
            clause = record["clause"]
            labels = dict(record["labels"])
            is_eligible = bool(record["eligible"])
            direct = is_eligible and self._is_rule_direct(labels)
            selected = clause.id in selected_ids
            if is_eligible:
                eligible += 1
            if direct:
                rule_direct += 1
                reason = "RULE_BLOCKING" if labels.get("blocking_signal") else "RULE_SINGLE_DOMAIN"
            elif selected:
                llm_selected += 1
                reason = "RRF_SELECTED"
            elif is_eligible:
                deferred += 1
                reason = "DEFERRED_BY_RRF_BUDGET"
            else:
                reason = "OUT_OF_REQUIREMENT_SCOPE"
            labels["selected_candidate"] = selected
            labels["selection_reason"] = reason
            labels["policy_version"] = self._label_policy.version
            labels["policy_source"] = self._label_policy.source
            metadata = dict(clause.quality_metadata or {})
            metadata["node_labels"] = labels
            metadata["candidate_recall"] = {
                "policy_version": _POLICY_VERSION,
                "rrf_k": _RRF_K,
                "route_weights": _ROUTE_WEIGHTS,
                "routes": record["routes"],
                "rrf_score": round(record["rrf_score"], 10),
                "keyword_score": record["keyword_score"],
                "llm_rank": record.get("llm_rank"),
                "selected_for_llm": selected,
                "decision": reason,
                "hybrid_status": hybrid_status,
            }
            clause.quality_metadata = metadata
            clause.mandatory_signal = bool(labels.get("mandatory_signal"))
        return ClauseCandidateRecallSummary(
            total,
            eligible,
            rule_direct,
            llm_selected,
            deferred,
            hybrid_status,
        )
