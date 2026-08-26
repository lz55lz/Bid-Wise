from types import SimpleNamespace
from uuid import UUID, uuid4

from app.services.clause_candidate_recall_service import ClauseCandidateRecallService
from app.services.node_label_policy import NodeLabelPolicy


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class _Clauses:
    def __init__(self, clauses: list[SimpleNamespace]) -> None:
        self._clauses = clauses

    def list_for_version(self, _version_id: UUID) -> list[SimpleNamespace]:
        return self._clauses


class _Documents:
    def __init__(self, project_id: UUID) -> None:
        self._version = SimpleNamespace(document_id=uuid4(), cleaning_summary={})
        self._document = SimpleNamespace(project_id=project_id)

    def get_version(self, _version_id: UUID) -> SimpleNamespace:
        return self._version

    def get_document(self, _document_id: UUID) -> SimpleNamespace:
        return self._document


def _clause(
    content: str,
    *,
    order_no: int,
    source_node_id: UUID | None = None,
) -> SimpleNamespace:
    source_node_id = source_node_id or uuid4()
    return SimpleNamespace(
        id=uuid4(),
        order_no=order_no,
        content=content,
        section_path="第三章 投标人须知",
        quality_metadata={"source_node_ids": [str(source_node_id)]},
        mandatory_signal=False,
    )


def _service(
    clauses: list[SimpleNamespace], *, llm_budget: int = 24
) -> tuple[ClauseCandidateRecallService, _Session, _Documents]:
    session = _Session()
    documents = _Documents(uuid4())
    service = ClauseCandidateRecallService(
        session, label_policy=NodeLabelPolicy(), llm_budget=llm_budget
    )
    service._clauses = _Clauses(clauses)  # type: ignore[assignment]
    service._documents = documents  # type: ignore[assignment]
    return service, session, documents


def test_clause_recall_keeps_rules_out_of_llm_budget_and_persists_audit() -> None:
    deterministic = _clause("投标人必须提供有效的建筑业企业资质证书。", order_no=1)
    ambiguous = _clause(
        "投标人必须同时提交近三年类似业绩证明和技术方案评分材料。", order_no=2
    )
    deferred = _clause("评标办法中技术方案得分按评分标准执行。", order_no=3)
    service, session, documents = _service([deterministic, ambiguous, deferred], llm_budget=1)

    # The optional hybrid route is deliberately made available here without
    # invoking an external embedding service; the unit test verifies that its
    # rank is recorded and fused with the deterministic routes.
    hybrid_node_id = ambiguous.quality_metadata["source_node_ids"][0]
    service._hybrid_ranks_by_source_node = (  # type: ignore[method-assign]
        lambda *_args: ({hybrid_node_id: {"qualification": 1}}, "AVAILABLE")
    )

    summary = service.select_for_extraction(uuid4())

    assert summary.rule_direct_clauses == 1
    assert summary.llm_selected_clauses == 1
    assert summary.deferred_clauses == 1
    assert summary.hybrid_status == "AVAILABLE"
    assert session.commits == 1

    deterministic_labels = deterministic.quality_metadata["node_labels"]
    assert deterministic_labels["selected_candidate"] is False
    assert deterministic_labels["selection_reason"] == "RULE_SINGLE_DOMAIN"

    recall = ambiguous.quality_metadata["candidate_recall"]
    assert recall["selected_for_llm"] is True
    assert recall["decision"] == "RRF_SELECTED"
    assert recall["routes"]["hybrid"]["profiles"] == {"qualification": 1}
    assert recall["rrf_score"] > 0
    assert (
        deferred.quality_metadata["candidate_recall"]["decision"]
        == "DEFERRED_BY_RRF_BUDGET"
    )
    assert (
        documents._version.cleaning_summary["clause_candidate_recall"]["llm_selected_clauses"]
        == 1
    )


def test_clause_recall_excludes_non_bidder_process_text() -> None:
    process_only = _clause("重新招标后投标人仍少于三家的，招标人应当依法重新招标。", order_no=1)
    service, _session, _documents = _service([process_only])

    summary = service.select_for_extraction(uuid4())

    assert summary.eligible_clauses == 0
    assert (
        process_only.quality_metadata["candidate_recall"]["decision"]
        == "OUT_OF_REQUIREMENT_SCOPE"
    )
    assert process_only.quality_metadata["node_labels"]["analysis_scope"] == "NON_BIDDER_PROCESS"
