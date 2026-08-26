from types import SimpleNamespace
from uuid import uuid4

from app.services.tender_clause_service import TenderClauseService


class _Documents:
    def __init__(self, nodes: list[SimpleNamespace]) -> None:
        self._nodes = nodes

    def list_nodes(self, *_args: object) -> list[SimpleNamespace]:
        return self._nodes


class _Evidences:
    @staticmethod
    def list_for_version(*_args: object) -> list[SimpleNamespace]:
        return []


class _Clauses:
    def __init__(self) -> None:
        self.clauses: list[object] = []
        self.links: list[object] = []

    def replace_for_version(
        self, _version_id: object, clauses: list[object], links: list[object]
    ) -> None:
        self.clauses = clauses
        self.links = links


class _Session:
    def commit(self) -> None:
        pass


def _node(
    content: str,
    *,
    node_type: str = "PARAGRAPH",
    source_type: str = "paragraph",
    selected: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        node_type=node_type,
        page_number=12,
        section_path="第四章 合同条款",
        cleaned_content=content,
        tender_req_candidate=selected,
        cleaning_metadata={
            "indexable": True,
            "node_labels": {
                "domains": ["BUSINESS"],
                "analysis_scope": "BIDDER_REQUIREMENT",
                "mandatory_signal": True,
                "quantitative_signal": False,
                "requirement_candidate": True,
            },
        },
        metadata_={"source_chunk_type": source_type},
    )


def test_clause_rebuild_preserves_atomic_parser_boundaries_and_labels() -> None:
    heading = _node("4.1 付款方式", node_type="SECTION")
    first_payment = _node("4.1.1 第一次支付：验收合格后支付。", source_type="clause")
    second_payment = _node("第二次支付：质保期满后支付。", source_type="clause")
    clauses = _Clauses()
    service = object.__new__(TenderClauseService)
    service._session = _Session()
    service._documents = _Documents([heading, first_payment, second_payment])
    service._evidences = _Evidences()
    service._clauses = clauses

    count = service.rebuild(uuid4())

    assert count == 2
    assert [clause.content for clause in clauses.clauses] == [
        first_payment.cleaned_content, second_payment.cleaned_content,
    ]
    assert clauses.clauses[0].quality_metadata["source_chunk_types"] == ["clause"]
    assert clauses.clauses[0].quality_metadata["node_labels"] == {
        "domains": ["BUSINESS"],
        "matched_tag_codes": [],
        "policy_versions": [],
        "analysis_scope": "BIDDER_REQUIREMENT",
        "analysis_scopes": ["BIDDER_REQUIREMENT"],
        "mandatory_signal": True,
        "blocking_signal": False,
        "quantitative_signal": False,
        "requirement_candidate": True,
        "selected_candidate": True,
    }


def test_clause_rebuild_splits_only_explicit_boundaries_not_character_length() -> None:
    combined = _node(
        "3.4.3 招标人应当退还投标保证金。"
        "3.4.4 投标人撤销投标文件的，保证金不予退还。"
        "3.5.1 投标人必须按时递交投标文件。"
    )
    long_single_clause = _node("4.1 投标人必须提供技术响应。" + "技术要求" * 800)
    clauses = _Clauses()
    service = object.__new__(TenderClauseService)
    service._session = _Session()
    service._documents = _Documents([combined, long_single_clause])
    service._evidences = _Evidences()
    service._clauses = clauses

    count = service.rebuild(uuid4())

    assert count == 4
    assert [clause.content for clause in clauses.clauses[:3]] == [
        "3.4.3 招标人应当退还投标保证金。",
        "3.4.4 投标人撤销投标文件的，保证金不予退还。",
        "3.5.1 投标人必须按时递交投标文件。",
    ]
    assert clauses.clauses[3].content == long_single_clause.cleaned_content
