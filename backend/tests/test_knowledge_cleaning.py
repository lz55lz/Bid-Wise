"""知识文件目录清洗与公开接口的回归测试。"""

import asyncio
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from app.api.v1.knowledge import KnowledgeCreateRequest, router
from app.integrations.mineru import ParsedNode
from app.modules.analysis.state_machine import (
    InvalidAnalysisTransition,
    transition_finding,
    transition_full,
)
from app.modules.conversations.retrieval_plan import plan_retrieval
from app.modules.documents.evidence_chunking import build_evidence_chunks
from app.modules.documents.cleaning_service import DocumentCleaningService
from app.modules.documents.models import DocumentNode
from app.modules.documents.state_machine import InvalidDocumentTransition
from app.modules.documents.state_machine import transition as document_transition
from app.modules.evaluation.state_machine import transition as evaluation_transition
from app.modules.identity.service import AuthenticatedUser
from app.modules.indexing.state_machine import transition as index_transition
from app.modules.knowledge.cleaning_service import clean_knowledge_nodes
from app.modules.knowledge.models import KnowledgeDocumentVersion, KnowledgeEntry, KnowledgeVersion
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.state_machine import transition as knowledge_transition
from app.modules.reports.state_machine import transition as report_transition
from app.modules.retrieval.query_rewrite import QueryIntent, build_bm25_queries, classify_query
from app.modules.retrieval.rag_service import ProjectRagService
from app.modules.retrieval.ranked_service import RankedEvidenceRetrievalService
from app.modules.retrieval.schemas import EvidenceSearchHit
from app.modules.tender_analysis.state_machine import InvalidTenderPipelineTransition
from app.modules.tender_analysis.state_machine import transition as tender_transition


class KnowledgeCleaningTests(unittest.TestCase):
    @staticmethod
    def _document_node(
        order_no: int,
        content: str,
        *,
        section_path: str = "第一章 / 投标人须知",
        node_type: str = "PARAGRAPH",
        page_number: int | None = 1,
    ) -> DocumentNode:
        return DocumentNode(
            id=uuid4(),
            order_no=order_no,
            content=content,
            cleaned_content=content,
            section_path=section_path,
            node_type=node_type,
            page_number=page_number,
        )

    def test_empty_table_template_is_not_indexable_content(self) -> None:
        template = (
            "<table><tr><td>序号</td><td>偏差说明</td></tr>"
            "<tr><td>1</td><td></td></tr><tr><td>2</td><td></td></tr>"
            "<tr><td>……</td><td></td></tr></table>"
        )
        populated = (
            "<table><tr><td>序号</td><td>偏差说明</td></tr>"
            "<tr><td>1</td><td>完全响应</td></tr></table>"
        )
        self.assertTrue(DocumentCleaningService._is_empty_template_table(template))
        self.assertFalse(DocumentCleaningService._is_empty_template_table(populated))

    def test_pipeline_state_machines_allow_only_declared_transitions(self) -> None:
        document = type("Version", (), {"parse_status": "UPLOADED"})()
        document_transition(document, "QUEUED")
        document_transition(document, "PARSING")
        document_transition(document, "CLEANING")
        self.assertEqual(document.parse_status, "CLEANING")
        with self.assertRaises(InvalidDocumentTransition):
            document_transition(document, "READY")

        knowledge = type("KnowledgeDocument", (), {"parse_status": "PARSING"})()
        knowledge_transition(knowledge, "INDEXING")
        knowledge_transition(knowledge, "READY")
        self.assertEqual(knowledge.parse_status, "READY")

        report = type("Report", (), {"status": "QUEUED"})()
        report_transition(report, "GENERATING")
        report_transition(report, "READY")
        self.assertEqual(report.status, "READY")

        index_job = type("IndexJob", (), {"status": "RUNNING"})()
        index_transition(index_job, "QUEUED")
        index_transition(index_job, "SUCCEEDED")
        self.assertEqual(index_job.status, "SUCCEEDED")

        finding = type("FindingAnalysisJob", (), {"status": "QUEUED"})()
        transition_finding(finding, "RUNNING")
        transition_finding(finding, "SUCCEEDED")
        self.assertEqual(finding.status, "SUCCEEDED")

        full = type("ProjectAnalysisRun", (), {"status": "RUNNING"})()
        transition_full(full, "REPORT_QUEUED")
        transition_full(full, "SUCCEEDED")
        self.assertEqual(full.status, "SUCCEEDED")
        with self.assertRaises(InvalidAnalysisTransition):
            transition_full(full, "QUEUED")

        evaluation = type("EvaluationRun", (), {"status": "QUEUED"})()
        evaluation_transition(evaluation, "RUNNING")
        evaluation_transition(evaluation, "SUCCEEDED")
        self.assertEqual(evaluation.status, "SUCCEEDED")

        tender = type("TenderPipelineRun", (), {"status": "RUNNING"})()
        tender_transition(tender, "WAITING_HUMAN_REVIEW")
        tender_transition(tender, "RESUME_QUEUED")
        tender_transition(tender, "RUNNING")
        tender_transition(tender, "SUCCEEDED")
        self.assertEqual(tender.status, "SUCCEEDED")
        with self.assertRaises(InvalidTenderPipelineTransition):
            tender_transition(tender, "RUNNING")

    def test_tender_evidence_merges_consecutive_paragraphs_but_keeps_boundaries(self) -> None:
        nodes = [
            self._document_node(1, "投标人应当具备相应资质。"),
            self._document_node(2, "投标文件应按要求密封并在截止时间前递交。"),
            self._document_node(3, "3.2.1 投标保证金应在递交截止时间前到账。"),
            self._document_node(4, "评标委员会将核验到账情况。"),
            self._document_node(5, "报价清单", node_type="TABLE"),
            self._document_node(6, "第二章 / 合同条款内容。", section_path="第二章 / 合同条款"),
        ]

        chunks = build_evidence_chunks(nodes)

        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[0].source_node_ids, (nodes[0].id, nodes[1].id))
        self.assertIn("相应资质", chunks[0].text)
        self.assertIn("密封", chunks[0].text)
        self.assertEqual(chunks[1].source_node_ids, (nodes[2].id, nodes[3].id))
        self.assertEqual(chunks[2].node_types, ("TABLE",))
        self.assertEqual(chunks[3].section_path, "第二章 / 合同条款")

    def test_tender_evidence_merges_short_numbered_children_with_parent_clause(self) -> None:
        nodes = [
            self._document_node(1, "1.4.1 投标人应具备承担本项目的资质、能力和信誉。"),
            self._document_node(2, "(1)资质要求：见投标人须知前附表。"),
            self._document_node(3, "(2)财务要求：见投标人须知前附表。"),
            self._document_node(4, "(3)业绩要求：见投标人须知前附表。"),
            self._document_node(5, "1.4.2 联合体投标应符合本章规定。"),
        ]

        chunks = build_evidence_chunks(nodes)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].source_node_ids, tuple(node.id for node in nodes[:4]))
        self.assertEqual(chunks[0].clause_keys, ("1.4.1", "1.4.1.1", "1.4.1.2", "1.4.1.3"))
        self.assertEqual(chunks[0].parent_clause_keys, ("1.4", "1.4.1"))
        self.assertEqual(chunks[1].clause_keys, ("1.4.2",))

    def test_tender_evidence_merges_contiguous_short_sibling_clauses(self) -> None:
        nodes = [
            self._document_node(
                1,
                "1.1.1 根据有关法律、法规和规章的规定，本招标项目已具备招标条件。",
                section_path="第一章 / 1.1 招标项目概况",
            ),
            self._document_node(
                2, "1.1.2 招标人：见投标人须知前附表。", section_path="第一章 / 1.1 招标项目概况"
            ),
            self._document_node(
                3,
                "1.1.3 招标代理机构：见投标人须知前附表。",
                section_path="第一章 / 1.1 招标项目概况",
            ),
            self._document_node(
                4,
                "1.1.4 招标项目名称：见投标人须知前附表。",
                section_path="第一章 / 1.1 招标项目概况",
            ),
            self._document_node(
                5, "1.2.1 资金来源：见投标人须知前附表。", section_path="第一章 / 1.2 资金来源"
            ),
        ]

        chunks = build_evidence_chunks(nodes)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].source_node_ids, tuple(node.id for node in nodes[:4]))
        self.assertEqual(chunks[0].clause_keys, ("1.1.1", "1.1.2", "1.1.3", "1.1.4"))
        self.assertEqual(chunks[0].parent_clause_keys, ("1.1",))
        self.assertEqual(chunks[1].clause_keys, ("1.2.1",))

    def test_contents_entries_are_excluded_but_body_first_chapter_remains(self) -> None:
        result = clean_knowledge_nodes(
            (
                ParsedNode("text", "目录"),
                ParsedNode("text", "第一章 总则 1"),
                ParsedNode("text", "第二章 招标 5"),
                ParsedNode("text", "第十章 附则 21"),
                ParsedNode("text", "第一章 总则"),
                ParsedNode("text", "本法适用于中华人民共和国境内的招标投标活动。"),
                ParsedNode("text", "  \n\t "),
            )
        )

        self.assertEqual(result.filtered_contents_entries, 4)
        self.assertEqual(result.filtered_empty_nodes, 1)
        self.assertEqual([node.content for node in result.nodes], [
            "第一章 总则",
            "本法适用于中华人民共和国境内的招标投标活动。",
        ])

    def test_contents_section_path_is_removed(self) -> None:
        result = clean_knowledge_nodes(
            (ParsedNode("text", "正文", section_path="目录 / 第一章"),)
        )
        self.assertEqual(result.nodes[0].section_path, "第一章")

    def test_publish_and_retry_routes_are_under_knowledge_entries(self) -> None:
        paths = {route.path for route in router.routes}
        self.assertIn("/knowledge-entries/versions/{version_id}/publish", paths)
        self.assertIn("/knowledge-entries/{entry_id}/documents/{document_id}/parse", paths)

    def test_date_only_metadata_is_accepted(self) -> None:
        payload = KnowledgeCreateRequest.model_validate(
            {
                "knowledge_type": "LEGAL",
                "title": "测试法规",
                "source_reference": "测试来源",
                "content": "正文",
                "issued_on": "2026-09-01",
                "effective_on": "2026-09-02",
                "citation_note": "测试引用",
            }
        )
        self.assertEqual(payload.issued_on.date().isoformat(), "2026-09-01")
        self.assertEqual(payload.effective_on.date().isoformat(), "2026-09-02")

    def test_serialized_entry_includes_metadata_and_cleaning_summary(self) -> None:
        now = datetime.now(UTC)
        entry = KnowledgeEntry(
            title="测试法规",
            knowledge_type="LEGAL",
            source_reference="测试来源",
            issued_on=now,
            effective_on=now,
            citation_note="测试引用",
        )
        version = KnowledgeVersion(
            version_no=1,
            status="DRAFT",
            content="正文",
            title="测试法规",
            source_reference="测试来源",
            issued_on=now,
            effective_on=now,
            citation_note="测试引用",
            created_at=now,
        )
        document = KnowledgeDocumentVersion(
            parse_status="READY",
            cleaning_summary={"filtered_contents_entries": 2, "indexed_nodes": 5},
        )
        row = KnowledgeService._row(entry, version, document)
        self.assertEqual(row["citation_note"], "测试引用")
        self.assertEqual(
            row["source_cleaning_summary"],
            {"filtered_contents_entries": 2, "indexed_nodes": 5},
        )

    def test_search_includes_title_source_and_content(self) -> None:
        session = AsyncMock()
        result = type("Result", (), {"all": lambda self: []})()
        session.execute.return_value = result
        service = KnowledgeService(session, AsyncMock())

        asyncio.run(
            service.list(
                AuthenticatedUser(uuid4(), "tester", "Tester", frozenset({"SYSTEM_ADMIN"})),
                "关键字",
            )
        )
        sql = str(session.execute.call_args.args[0])
        self.assertIn("knowledge_versions.title", sql)
        self.assertIn("knowledge_versions.source_reference", sql)
        self.assertIn("knowledge_versions.content", sql)
        self.assertIn(" OR ", sql)

    def test_bm25_query_plan_keeps_user_terms_without_synonym_injection(self) -> None:
        queries = build_bm25_queries("中标后如何进行合同签订？")

        self.assertIn("中标后如何进行合同签订？", queries)
        self.assertIn("中标后如何进行合同签订", queries)
        self.assertFalse(any("中标合同" in item for item in queries))
        self.assertIs(classify_query("中标后如何进行合同签订？"), QueryIntent.PROCEDURAL)

    def test_bound_project_keeps_project_evidence_when_law_is_supplemental(self) -> None:
        plan = plan_retrieval("中标后如何进行合同签订？")

        self.assertEqual(plan.mode, "HYBRID")
        self.assertTrue(plan.project)
        self.assertTrue(plan.legal)

    def test_risk_question_does_not_require_report_when_evidence_can_answer(self) -> None:
        plan = plan_retrieval("项目当前有哪些主要风险和待办？")

        self.assertTrue(plan.project)
        self.assertFalse(plan.report)

    def test_explicit_report_question_requires_report_and_project_evidence(self) -> None:
        plan = plan_retrieval("分析报告中有哪些主要风险？")

        self.assertTrue(plan.project)
        self.assertTrue(plan.report)

    def test_rag_contexts_respect_global_budget(self) -> None:
        first = type(
            "Hit", (), {"evidence_id": uuid4(), "parent_context": None, "quoted_text": "甲" * 8}
        )()
        second = type(
            "Hit", (), {"evidence_id": uuid4(), "parent_context": None, "quoted_text": "乙" * 8}
        )()

        contexts = ProjectRagService.bounded_contexts([first, second], budget=12)

        self.assertEqual(len(contexts), 2)
        self.assertEqual(sum(len(item["content"]) for item in contexts), 12)

    def test_knowledge_bm25_is_limited_to_published_entries(self) -> None:
        session = AsyncMock()
        result = type("Result", (), {"all": lambda self: []})()
        session.execute.return_value = result

        asyncio.run(KnowledgeRepository(session).search_published_bm25("合同签订", 5))

        sql = str(session.execute.call_args.args[0])
        self.assertIn("plainto_tsquery", sql)
        self.assertIn("to_tsvector", sql)
        self.assertIn("knowledge_versions.status", sql)

    def test_mmr_moves_distinct_evidence_ahead_of_near_duplicate(self) -> None:
        duplicate = EvidenceSearchHit(
            evidence_id=uuid4(),
            quoted_text="guarantee payment deadline requirement confirmation",
            locator={},
            similarity=0.94,
        )
        first = EvidenceSearchHit(
            evidence_id=uuid4(),
            quoted_text="guarantee payment deadline requirement",
            locator={},
            similarity=0.95,
        )
        distinct = EvidenceSearchHit(
            evidence_id=uuid4(),
            quoted_text="contract signing notice obligation",
            locator={},
            similarity=0.80,
        )

        result = RankedEvidenceRetrievalService._mmr_diversify(
            [(first, 0.95), (duplicate, 0.94), (distinct, 0.80)], 3
        )

        self.assertEqual(result[:2], [first, distinct])


if __name__ == "__main__":
    unittest.main()
