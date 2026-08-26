"""Build business clauses from cleaned layout nodes without crossing sections or tables."""

import hashlib
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import ClauseEvidence, TenderClause
from app.db.repositories.clause_repository import ClauseRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.evidence_repository import EvidenceRepository
from app.services.document_ingest.semantic_boundaries import (
    split_explicit_clause_boundaries,
)

_MANDATORY = re.compile(r"不得|必须|应当|须|否决|废标|无效|不予|签章|应具备|应满足")
_STRUCTURAL_NODE_TYPES = frozenset({"TABLE", "CELL", "LIST"})


def _node_labels(node: object) -> dict:
    """Return the persisted node label without trusting incomplete historical rows."""
    metadata = getattr(node, "cleaning_metadata", None) or {}
    labels = metadata.get("node_labels") or {}
    return labels if isinstance(labels, dict) else {}


def _source_chunk_type(node: object) -> str:
    metadata = getattr(node, "metadata_", None) or {}
    source_type = metadata.get("source_chunk_type") or metadata.get("chunk_type") or ""
    return str(source_type).lower()


def _full_section_path(node: object) -> str:
    """Return the preserved source path rather than a database display truncation."""
    metadata = getattr(node, "metadata_", None) or {}
    source_path = metadata.get("source_section_path")
    if isinstance(source_path, str) and source_path:
        return source_path
    return str(getattr(node, "section_path", None) or "")


def _quality_metadata(nodes: list[object]) -> dict:
    """Carry parser and classifier facts from layout nodes to the clause layer."""
    labels = [_node_labels(node) for node in nodes]
    domains = sorted({
        str(domain)
        for label in labels
        for domain in label.get("domains", [])
        if domain
    })
    matched_tag_codes = sorted({
        str(code)
        for label in labels
        for code in label.get("matched_tag_codes", [])
        if code
    })
    policy_versions = sorted({
        str(label["policy_version"])
        for label in labels
        if label.get("policy_version")
    })
    analysis_scopes = sorted({
        str(label["analysis_scope"])
        for label in labels
        if label.get("analysis_scope")
    })
    # A joined clause may include surrounding context.  Prefer a bidder-facing
    # scope when any source node carries one; otherwise preserve the strict
    # non-bidder classification so contract/evaluator text cannot enter the
    # material-matching Requirement path after clause aggregation.
    if "BIDDER_REQUIREMENT" in analysis_scopes:
        analysis_scope = "BIDDER_REQUIREMENT"
    elif "SCORING_CRITERIA" in analysis_scopes:
        analysis_scope = "SCORING_CRITERIA"
    elif "NON_BIDDER_PROCESS" in analysis_scopes:
        analysis_scope = "NON_BIDDER_PROCESS"
    elif analysis_scopes:
        analysis_scope = analysis_scopes[0]
    else:
        analysis_scope = None
    return {
        "source_node_ids": [str(node.id) for node in nodes],
        "source_node_count": len(nodes),
        "source_chunk_types": sorted({
            _source_chunk_type(node) for node in nodes if _source_chunk_type(node)
        }),
        "node_labels": {
            "domains": domains,
            "matched_tag_codes": matched_tag_codes,
            "policy_versions": policy_versions,
            "analysis_scope": analysis_scope,
            "analysis_scopes": analysis_scopes,
            "mandatory_signal": any(bool(label.get("mandatory_signal")) for label in labels),
            "blocking_signal": any(bool(label.get("blocking_signal")) for label in labels),
            "quantitative_signal": any(bool(label.get("quantitative_signal")) for label in labels),
            "requirement_candidate": any(
                bool(label.get("requirement_candidate")) for label in labels
            ),
            # The clean-stage budget controls ambiguous LLM input. Preserve it
            # separately from semantics: explicit blocking clauses may bypass
            # this budget through deterministic rule extraction.
            "selected_candidate": any(
                bool(getattr(node, "tender_req_candidate", False)) for node in nodes
            ),
        },
    }


class TenderClauseService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._documents = DocumentRepository(session)
        self._evidences = EvidenceRepository(session)
        self._clauses = ClauseRepository(session)

    def rebuild(self, document_version_id: UUID) -> int:
        nodes = [
            node for node in self._documents.list_nodes(document_version_id, 0, 1_000_000)
            if (
                node.cleaning_metadata.get("indexable")
                and node.cleaned_content
                and node.node_type.upper() != "SECTION"
            )
        ]
        evidence_by_node = {
            evidence.document_node_id: evidence.id
            for evidence in self._evidences.list_for_version(document_version_id)
            if evidence.document_node_id is not None
        }
        clauses: list[TenderClause] = []
        links: list[ClauseEvidence] = []


        def add_clause(node: object, content: str) -> None:
            normalized_content = content.strip()
            if not normalized_content:
                return
            section_path = _full_section_path(node) or None
            page_number = getattr(node, "page_number", None)
            clause = TenderClause(
                id=uuid4(), document_version_id=document_version_id, order_no=len(clauses),
                clause_type="REQUIREMENT" if _MANDATORY.search(normalized_content) else "TEXT",
                section_path=section_path, start_page=page_number,
                end_page=page_number, content=normalized_content,
                contextualized_content=(f"章节：{section_path}\n" if section_path else "")
                + normalized_content,
                content_hash=hashlib.sha256(normalized_content.encode()).hexdigest(),
                mandatory_signal=bool(_MANDATORY.search(normalized_content)),
                quality_metadata=_quality_metadata([node]),
                created_at=datetime.now(UTC),
            )
            clauses.append(clause)
            evidence_id = evidence_by_node.get(node.id)
            if evidence_id:
                links.append(
                    ClauseEvidence(
                        clause_id=clause.id,
                        evidence_id=evidence_id,
                        relation="DERIVED_FROM",
                    )
                )

        for node in nodes:
            content = node.cleaned_content.strip()
            if node.node_type.upper() in _STRUCTURAL_NODE_TYPES:
                add_clause(node, content)
                continue
            # A DocumentNode is an immutable MinerU layout fact.  Derive one
            # clause from it, or several clauses only when the source text
            # itself contains explicit numbered/staged boundaries.  Do not
            # join adjacent nodes and never flush based on character count.
            for clause_content in split_explicit_clause_boundaries(content):
                add_clause(node, clause_content)
        self._clauses.replace_for_version(document_version_id, clauses, links)
        self._session.commit()
        return len(clauses)
