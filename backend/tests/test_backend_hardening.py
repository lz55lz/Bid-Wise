"""Agent 流式、动态批处理、上传内容与匹配不确定性的关键回归测试。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zipfile import ZipFile

from app.core.upload_validation import validate_uploaded_file_content
from app.integrations import redis_pool
from app.integrations.llm import MiniMaxM3Client
from app.modules.conversations.assistant_context import AssistantRunContext
from app.modules.conversations.assistant_workflow import _ReasoningStreamFilter
from app.modules.conversations.retrieval_plan import (
    build_retrieval_query,
    plan_global_legal_retrieval,
    plan_retrieval,
)
from app.modules.conversations.schemas import ConversationCitation
from app.modules.conversations.service import ConversationService
from app.modules.identity.service import AuthenticatedUser
from app.modules.matching.evaluator import MaterialRequirementEvaluator
from app.modules.tender_analysis.extraction_service import TenderExtractionService
from app.modules.tender_analysis.service import TenderPipelineService
from app.modules.retrieval.structured_chunking import retrieval_text


class BackendHardeningTests(unittest.TestCase):
    def test_reasoning_filter_handles_tags_split_across_chunks(self) -> None:
        stream_filter = _ReasoningStreamFilter()
        chunks = ["<thi", "nk>内部", "推理</thi", "nk>正式", "答案"]
        visible = "".join(stream_filter.feed(chunk) for chunk in chunks) + stream_filter.finish()
        self.assertEqual(visible, "正式答案")

    def test_extraction_batches_obey_evidence_and_character_budget(self) -> None:
        evidences = [SimpleNamespace(quoted_text="甲" * 1_800) for _ in range(10)]
        batches = TenderExtractionService._build_batches(evidences)  # type: ignore[arg-type]
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(len(batch) <= 6 for batch in batches))
        self.assertTrue(
            all(
                sum(len(evidence.quoted_text) for evidence in batch) <= 9_000
                for batch in batches
            )
        )


    def test_finding_batches_obey_item_and_character_budget(self) -> None:
        evidences = [
            {"evidence_id": str(uuid4()), "content": "甲" * 1_500, "locator": {}}
            for _ in range(16)
        ]
        batches = MiniMaxM3Client._build_finding_batches(evidences)
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(len(batch) <= 10 for batch in batches))
        self.assertTrue(
            all(
                sum(len(str(item["content"])) + 160 for item in batch) <= 10_000
                for batch in batches
            )
        )

    def test_human_review_cannot_override_model_provenance(self) -> None:
        with self.assertRaises(Exception):
            TenderPipelineService._sanitize_reviewed_tags(
                {
                    "PROJECT_NAME": {
                        "value": "人工修正名称",
                        "source_node_id": str(uuid4()),
                    }
                }
            )
        sanitized = TenderPipelineService._sanitize_reviewed_tags(
            {"PROJECT_NAME": {"value": "人工修正名称", "note": "已核对原文"}}
        )
        self.assertEqual(
            sanitized,
            {"PROJECT_NAME": {"value": "人工修正名称", "note": "已核对原文"}},
        )

    def test_tender_review_selection_defaults_legacy_clients_to_all_candidates(self) -> None:
        run = SimpleNamespace(
            pending_review={"extracted_tags": {"PROJECT_NAME": {}, "BID_BOND": {}}}
        )

        self.assertEqual(
            TenderPipelineService._approved_tag_codes(run, ["BID_BOND"]), {"BID_BOND"}
        )
        self.assertEqual(
            TenderPipelineService._approved_tag_codes(run, None), {"PROJECT_NAME", "BID_BOND"}
        )

    def test_table_retrieval_text_keeps_headers_with_each_row_value(self) -> None:
        text = (
            "<table><tr><td>包件号</td><td>物资名称</td><td>数量</td></tr>"
            "<tr><td>JC09</td><td>避雷器</td><td>128</td></tr></table>"
        )

        rendered = retrieval_text(text, ("TABLE",))

        self.assertIn("表格字段：包件号；物资名称；数量", rendered)
        self.assertIn("包件号：JC09；物资名称：避雷器；数量：128", rendered)

    def test_pdf_signature_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "valid.pdf"
            valid.write_bytes(b"%PDF-1.7\nbody")
            validate_uploaded_file_content(valid, ".pdf")

            invalid = Path(directory) / "fake.pdf"
            invalid.write_bytes(b"not-a-pdf")
            with self.assertRaises(Exception):
                validate_uploaded_file_content(invalid, ".pdf")

    def test_ooxml_structure_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "valid.docx"
            with ZipFile(valid, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types />")
                archive.writestr("word/document.xml", "<document />")
            validate_uploaded_file_content(valid, ".docx")

            invalid = Path(directory) / "fake.docx"
            with ZipFile(invalid, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types />")
                archive.writestr("xl/workbook.xml", "<workbook />")
            with self.assertRaises(Exception):
                validate_uploaded_file_content(invalid, ".docx")

    def test_casual_message_skips_fact_retrieval(self) -> None:
        plan = plan_retrieval("你好！")
        self.assertEqual(plan.mode, "CASUAL")
        self.assertFalse(plan.project)
        self.assertEqual(plan.required_sources, frozenset())

    def test_substantive_question_always_recalls_user_preferences(self) -> None:
        plan = plan_retrieval("当前绑定企业适配这个项目吗？")
        self.assertTrue(plan.memory)

    def test_hybrid_question_plans_project_legal_and_report_sources(self) -> None:
        plan = plan_retrieval("请结合项目法规和分析报告判断保证金风险")
        self.assertTrue(plan.project)
        self.assertTrue(plan.legal)
        self.assertTrue(plan.report)
        self.assertEqual(
            plan.required_sources,
            frozenset({"PROJECT_EVIDENCE", "LEGAL_KNOWLEDGE", "PROJECT_REPORT"}),
        )

    def test_report_and_legal_questions_only_require_their_actual_sources(self) -> None:
        report_plan = plan_retrieval("报告里怎么说？")
        self.assertTrue(report_plan.report)
        self.assertFalse(report_plan.project)
        self.assertEqual(report_plan.required_sources, frozenset({"PROJECT_REPORT"}))

        legal_plan = plan_retrieval("招投标法关于保证金有什么规定？")
        self.assertTrue(legal_plan.legal)
        self.assertFalse(legal_plan.project)
        self.assertEqual(legal_plan.required_sources, frozenset({"LEGAL_KNOWLEDGE"}))

    def test_enterprise_fit_question_uses_bound_enterprise_match_source(self) -> None:
        plan = plan_retrieval("绑定的企业适配度怎么样？")
        self.assertEqual(plan.mode, "ENTERPRISE_FIT")
        self.assertTrue(plan.enterprise_fit)
        self.assertFalse(plan.project)
        self.assertEqual(plan.required_sources, frozenset({"ENTERPRISE_FIT"}))

    def test_global_enterprise_fit_question_keeps_project_dependent_plan(self) -> None:
        plan = plan_global_legal_retrieval("当前绑定企业适配这个项目吗？", has_history=True)
        self.assertEqual(plan.mode, "ENTERPRISE_FIT")
        self.assertTrue(plan.enterprise_fit)

    def test_enterprise_fit_followup_keeps_match_context(self) -> None:
        plan = plan_retrieval("第一个缺口具体缺什么，现有哪份材料被拿来匹配？", has_enterprise_fit_history=True)
        self.assertEqual(plan.mode, "ENTERPRISE_FIT")
        self.assertTrue(plan.enterprise_fit)

    def test_enterprise_fit_context_does_not_require_a_forged_evidence_citation(self) -> None:
        context = AssistantRunContext(
            actor_id=uuid4(),
            role_codes=frozenset(),
            project_id=uuid4(),
            plan=plan_retrieval("绑定企业适配度怎么样？"),
            project_access_verified=True,
            enterprise_fit_items=[{"content": "已绑定企业和匹配结果"}],
        )
        self.assertTrue(ConversationService._citations_cover_plan([], context))

    def test_short_followup_retrieval_query_keeps_previous_user_topic(self) -> None:
        query = build_retrieval_query(
            "那它什么时候截止？",
            [("USER", "这个项目的投标截止时间是什么？"), ("ASSISTANT", "之前的回答")],
        )
        self.assertIn("这个项目的投标截止时间", query)
        self.assertIn("追问：那它什么时候截止", query)

    def test_required_source_coverage_rejects_missing_legal_citation(self) -> None:
        actor = AuthenticatedUser(
            id=uuid4(), username="tester", display_name="Tester", role_codes=frozenset()
        )
        context = AssistantRunContext(
            actor_id=actor.id,
            role_codes=actor.role_codes,
            project_id=uuid4(),
            plan=plan_retrieval("这个保证金规定是否合法合规？"),
            project_access_verified=True,
        )
        citations = [
            ConversationCitation(
                source="PROJECT_EVIDENCE",
                evidence_id=str(uuid4()),
                quoted_text="项目证据",
                locator={},
            )
        ]
        self.assertFalse(ConversationService._citations_cover_plan(citations, context))

    def test_multi_value_extraction_keeps_distinct_hits(self) -> None:
        extracted: dict[str, dict[str, object]] = {}
        TenderExtractionService._merge_multi_value_candidate(
            extracted,
            "QUAL_SIMILAR_EXPERIENCE",
            {
                "value": {"min_count": 2},
                "confidence": 0.8,
                "source_node_id": "node-a",
                "source_page_number": 1,
                "source_text": "要求两项业绩",
            },
        )
        TenderExtractionService._merge_multi_value_candidate(
            extracted,
            "QUAL_SIMILAR_EXPERIENCE",
            {
                "value": {"min_amount": "5000万元"},
                "confidence": 0.9,
                "source_node_id": "node-b",
                "source_page_number": 2,
                "source_text": "单项金额要求",
            },
        )
        self.assertEqual(len(extracted["QUAL_SIMILAR_EXPERIENCE"]["value"]), 2)
        self.assertEqual(extracted["QUAL_SIMILAR_EXPERIENCE"]["source_node_id"], "node-b")

    def test_registered_capital_compares_real_amount(self) -> None:
        requirement = SimpleNamespace(
            conditions={
                "items": [
                    {"dimension": "QUAL_REGISTERED_CAPITAL", "value": "不低于5000万元"}
                ]
            }
        )
        material = SimpleNamespace(
            status="CONFIRMED",
            valid_to=None,
            name="注册资本证明",
            amount=MaterialRequirementEvaluator._money_amount("3000万元"),
            level=None,
            material_type="FINANCE",
            attributes={"tag_codes": ["QUAL_REGISTERED_CAPITAL"]},
        )
        result = MaterialRequirementEvaluator().evaluate(
            requirement, material, None
        )  # type: ignore[arg-type]
        self.assertEqual(result.status, "MISSING")

    def test_personnel_tag_alone_does_not_create_false_positive(self) -> None:
        requirement = SimpleNamespace(
            conditions={
                "items": [
                    {
                        "dimension": "QUAL_PERSONNEL",
                        "value": [{"role": "项目经理", "cert": "一级注册建造师", "min_exp": "8年"}],
                    }
                ]
            }
        )
        material = SimpleNamespace(
            status="CONFIRMED",
            valid_to=None,
            name="人员材料",
            amount=None,
            level=None,
            material_type="PERSONNEL",
            attributes={"tag_codes": ["QUAL_PERSONNEL"]},
        )
        result = MaterialRequirementEvaluator().evaluate(
            requirement, material, None
        )  # type: ignore[arg-type]
        self.assertEqual(result.status, "UNCERTAIN")


class RedisPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await redis_pool.close_redis_pool()

    async def asyncTearDown(self) -> None:
        await redis_pool.close_redis_pool()

    async def test_invalidation_waits_for_active_borrower_before_closing_pool(self) -> None:
        pool = SimpleNamespace(close=AsyncMock())
        with patch("app.integrations.redis_pool.create_pool", AsyncMock(return_value=pool)):
            async with redis_pool.borrow_redis_pool("redis://localhost:6379/0"):
                await redis_pool.invalidate_redis_pool()
                pool.close.assert_not_awaited()
            pool.close.assert_awaited_once()

    def test_personnel_structured_facts_can_match(self) -> None:
        requirement = SimpleNamespace(
            conditions={
                "items": [
                    {
                        "dimension": "QUAL_PERSONNEL",
                        "value": [{"role": "项目经理", "cert": "一级注册建造师", "min_exp": "8年"}],
                    }
                ]
            }
        )
        material = SimpleNamespace(
            status="CONFIRMED",
            valid_to=None,
            name="人员材料",
            amount=None,
            level=None,
            material_type="PERSONNEL",
            attributes={
                "tag_codes": ["QUAL_PERSONNEL"],
                "persons": [
                    {"role": "项目经理", "certificate": "一级注册建造师", "experience_years": 12}
                ],
            },
        )
        result = MaterialRequirementEvaluator().evaluate(
            requirement, material, None
        )  # type: ignore[arg-type]
        self.assertEqual(result.status, "MATCHED")

    def test_matching_returns_uncertain_when_relevant_material_lacks_semantic_fact(self) -> None:
        requirement = SimpleNamespace(
            conditions={"items": [{"dimension": "QUAL_SIMILAR_EXPERIENCE", "value": True}]}
        )
        material = SimpleNamespace(
            status="CONFIRMED",
            valid_to=None,
            name="某智慧交通项目业绩",
            amount=None,
            level=None,
            material_type="PROJECT_EXPERIENCE",
            attributes={},
        )
        result = MaterialRequirementEvaluator().evaluate(
            requirement, material, None
        )  # type: ignore[arg-type]
        self.assertEqual(result.status, "UNCERTAIN")


if __name__ == "__main__":
    unittest.main()
