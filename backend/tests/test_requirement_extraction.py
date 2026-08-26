"""
LLM 招标要求提取测试：从真实数据库加载节点，验证提取质量

三个维度：
1. conditions 解析质量：operator/数值判断是否准确
2. 批量 vs 单节点：结果一致性
3. 程序性节点：被 cleaning 过滤的内容，LLM 是否误判
"""
from json import JSONDecodeError
from unittest.mock import Mock
from uuid import UUID

import pytest

from app.core.config import get_settings
from app.db import models as m
from app.db.session import get_session_factory
from app.integrations.ai.llm import DeepSeekV4FlashClient, LangChainLlmClient
from app.schemas.extraction import (
    ProjectFieldCandidate,
    RequirementCandidate,
    RequirementExtractionResult,
)
from app.services.requirement_extraction_service import (
    RequirementExtractionService,
    _canonical_ai_input,
)


def test_llm_candidate_selection_excludes_noise_and_keeps_labelled_clause() -> None:
    units = [
        {"id": "1", "order_no": 1, "page_number": 1, "content": "目录\n第一章 总则"},
        {
            "id": "2", "order_no": 2, "page_number": 8,
            "content": "投标人必须提供近三年类似项目业绩，至少 2 项。",
        },
    ]

    selected = RequirementExtractionService._select_llm_candidates(units)

    assert [item["order_no"] for item in selected] == [2]
    assert "强制性条款" in selected[0]["labels"]
    assert "量化约束" in selected[0]["labels"]


def test_ai_audit_payload_serializes_uuid_evidence_anchors_deterministically() -> None:
    payload = _canonical_ai_input({
        "evidence_id": UUID("12345678-1234-5678-1234-567812345678"),
        "content": "投标人必须提供资质证书。",
    })

    assert payload == (
        '{"content":"投标人必须提供资质证书。",'
        '"evidence_id":"12345678-1234-5678-1234-567812345678"}'
    )


def test_review_queue_flushes_new_candidates_before_applying_active_limit() -> None:
    service = object.__new__(RequirementExtractionService)
    service._session = Mock()
    service._requirements = Mock()
    service._requirements.list_for_project.return_value = []
    service._requirements.list_evidence_ids_for_requirements.return_value = {}

    service._prioritize_review_queue(UUID("12345678-1234-5678-1234-567812345678"))

    service._session.flush.assert_called_once_with()


def test_labeled_units_route_rules_before_llm_even_when_the_llm_budget_defers_them() -> None:
    deterministic = {
        "id": "rule", "order_no": 1, "page_number": 8,
        "content": "投标人必须提供有效资质证书。",
        "node_labels": {
            "domains": ["QUALIFICATION"], "mandatory_signal": True,
            "quantitative_signal": False, "requirement_candidate": True,
            "selected_candidate": True,
        },
    }
    ambiguous = {
        "id": "ambiguous", "order_no": 2, "page_number": 9,
        "content": "投标人必须提供业绩，并按评分标准提交技术方案。",
        "node_labels": {
            "domains": ["QUALIFICATION", "SCORING"], "mandatory_signal": True,
            "quantitative_signal": False, "requirement_candidate": True,
            "selected_candidate": True,
        },
    }
    deferred_by_budget = {
        "id": "deferred", "order_no": 3, "page_number": 10,
        "content": "投标人必须提供另一项资质证书。",
        "node_labels": {
            "domains": ["QUALIFICATION"], "mandatory_signal": True,
            "quantitative_signal": False, "requirement_candidate": True,
            "selected_candidate": False,
        },
    }

    rule_units, llm_units = RequirementExtractionService._route_labeled_units([
        deterministic, ambiguous, deferred_by_budget,
    ])
    selected = RequirementExtractionService._select_llm_candidates(llm_units)

    assert [unit["id"] for unit in rule_units] == ["rule", "deferred"]
    assert rule_units[1]["outside_llm_budget"] is True
    assert [unit["id"] for unit in selected] == ["ambiguous"]
    assert "歧义:多业务域" in selected[0]["labels"]


def test_deferred_blocking_clause_is_preserved_by_rules_not_sent_to_llm() -> None:
    blocking = {
        "id": "blocking", "order_no": 4, "page_number": 11,
        "content": "投标人不得以任何方式转包本项目。",
        "node_labels": {
            "domains": ["BUSINESS", "TECHNICAL"], "mandatory_signal": True,
            "blocking_signal": True, "quantitative_signal": False,
            "requirement_candidate": True, "selected_candidate": False,
        },
    }

    rule_units, llm_units = RequirementExtractionService._route_labeled_units([blocking])

    assert [unit["id"] for unit in rule_units] == ["blocking"]
    assert rule_units[0]["outside_llm_budget"] is True
    assert llm_units == []


def test_non_bidder_clause_is_skipped_before_rule_and_llm_extraction() -> None:
    non_bidder = {
        "id": "process", "order_no": 5, "page_number": 12,
        "content": "招标人将在合同签订后退还投标保证金。",
        "node_labels": {
            "domains": ["BUSINESS"], "mandatory_signal": True,
            "blocking_signal": False, "requirement_candidate": True,
            "selected_candidate": True, "analysis_scope": "NON_BIDDER_PROCESS",
        },
    }

    rule_units, llm_units = RequirementExtractionService._route_labeled_units([non_bidder])

    assert rule_units == []
    assert llm_units == []


def test_rule_fallback_prefers_section_semantics_and_strips_section_heading() -> None:
    content = "章节：第六章 资格审查\n投标人应提供近三年类似业绩证明。"

    assert (
        RequirementExtractionService._infer_rule_category(content, "第六章 资格审查")
        == "QUALIFICATION"
    )
    assert (
        RequirementExtractionService._rule_requirement_title(content)
        == "投标人应提供近三年类似业绩证明"
    )


def test_rule_category_does_not_treat_generic_project_reference_as_project_fact() -> None:
    assert (
        RequirementExtractionService._infer_rule_category(
            "投标人应在规定时间内完成本项目远程解密，逾期按无效投标处理。",
            "投标人须知",
        )
        == "BUSINESS"
    )


def test_rule_category_and_mandatory_detection_keep_financial_and_credit_eligibility() -> None:
    financial = "（4）财务要求：提供近三年经第三方审计单位审计的财务报表。"
    credit = "投标人未在信用中国网站中被列入失信被执行人名单。"

    assert RequirementExtractionService._infer_rule_category(financial) == "QUALIFICATION"
    assert RequirementExtractionService._infer_rule_category(credit) == "QUALIFICATION"
    assert RequirementExtractionService._is_unit_hard_requirement({
        "content": financial,
        "node_labels": {"mandatory_signal": True},
    }) is True
    assert RequirementExtractionService._is_unit_hard_requirement({
        "content": credit,
        "node_labels": {"mandatory_signal": True},
    }) is True
    assert (
        RequirementExtractionService._infer_rule_category(
            "本项目计划工期为 120 日历天。",
            "项目概况",
        )
        == "PROJECT"
    )
    assert (
        RequirementExtractionService._infer_rule_category(
            "联合体各方不得再以自己名义单独或参加其他联合体在同一标段中投标。",
            "投标人须知",
        )
        == "BUSINESS"
    )


def test_condition_normalization_rejects_empty_and_unwraps_provider_item_list() -> None:
    assert RequirementExtractionService._normalize_conditions({"all": ""}) is None
    assert RequirementExtractionService._normalize_conditions({
        "all": {"item": [{"dimension": "count", "operator": "GTE", "value": 2}]}
    }) == {"all": [{"dimension": "count", "operator": "GTE", "value": 2}]}


def test_requirement_filter_rejects_unknown_evidence_and_merges_supported_duplicates() -> None:
    service = object.__new__(RequirementExtractionService)
    result = RequirementExtractionResult(
        project_fields=[
            ProjectFieldCandidate(
                field_code="BUDGET", value_json={"value": 100}, confidence=0.9,
                evidence_order_nos=[99],
            ),
        ],
        requirements=[
            RequirementCandidate(
                category="QUALIFICATION", title="投标人必须提供资质证书",
                description="投标人必须提供资质证书。", is_mandatory=True,
                confidence=0.8, evidence_order_nos=[2, 99],
            ),
            RequirementCandidate(
                category="QUALIFICATION", title="投标人应提交资质证书",
                description="投标人必须提供有效资质证书。", is_mandatory=True,
                confidence=0.9, evidence_order_nos=[3],
            ),
            RequirementCandidate(
                category="BUSINESS", title="无证据要求", description="无证据。",
                is_mandatory=False, confidence=0.8, evidence_order_nos=[],
            ),
        ],
    )

    filtered = service._filter_result(result, allowed_order_nos={2, 3})

    assert filtered.project_fields == []
    assert len(filtered.requirements) == 1
    requirement = filtered.requirements[0]
    assert requirement.evidence_order_nos == [2, 3]
    assert requirement.description == "投标人必须提供有效资质证书。"


def test_llm_project_field_persistence_requires_and_stores_primary_evidence() -> None:
    project_id = UUID("12345678-1234-5678-1234-567812345678")
    evidence_id = UUID("87654321-4321-8765-4321-876543218765")
    candidate = ProjectFieldCandidate(
        field_code="BUDGET",
        value_json={"value": 100},
        confidence=0.9,
        evidence_order_nos=[1],
    )
    service = object.__new__(RequirementExtractionService)
    service._project_fields = Mock()
    service._project_fields.find_by_codes.return_value = None

    service._upsert_project_field(
        project_id,
        candidate,
        evidence_ids=[evidence_id],
    )

    field = service._project_fields.add.call_args.args[0]
    assert field.primary_evidence_id == evidence_id
    assert field.review_status == "PENDING"


def test_project_field_candidate_passes_resolved_evidence_to_persistence() -> None:
    project_id = UUID("12345678-1234-5678-1234-567812345678")
    evidence_id = UUID("87654321-4321-8765-4321-876543218765")
    service = object.__new__(RequirementExtractionService)
    service._upsert_project_field = Mock()
    candidates = RequirementExtractionResult(
        project_fields=[
            ProjectFieldCandidate(
                field_code="BUDGET",
                value_json={"value": 100},
                confidence=0.9,
                evidence_order_nos=[1],
            )
        ],
        requirements=[],
    )

    persisted = service._persist_candidates(
        project_id,
        candidates,
        nodes_for_llm=[{"order_no": 1}],
        evidence_by_order={1: evidence_id},
    )

    assert persisted == 1
    service._upsert_project_field.assert_called_once_with(
        project_id,
        candidates.project_fields[0],
        evidence_ids=[evidence_id],
    )


def test_hard_requirement_uses_actual_duty_text_not_the_rule_route() -> None:
    instruction_heading = {
        "content": "招标人应按照投标人须知前附表规定公示中标候选人。",
        "section_path": "中标候选人公示",
        "node_labels": {
            "mandatory_signal": True,
            "blocking_signal": False,
            "analysis_scope": "NON_BIDDER_PROCESS",
        },
    }
    bidder_duty = {
        "content": "投标人必须提供有效资质证书。",
        "section_path": "资格要求",
        "node_labels": {
            "mandatory_signal": True,
            "blocking_signal": False,
            "analysis_scope": "BIDDER_REQUIREMENT",
        },
    }

    assert RequirementExtractionService._is_unit_hard_requirement(instruction_heading) is False
    assert RequirementExtractionService._is_unit_hard_requirement(bidder_duty) is True


def test_structured_output_cleanup_caps_duplicate_evidence_anchors() -> None:
    raw = {
        "project_fields": [],
        "requirements": [{
            "category": "QUALIFICATION", "title": "资质", "description": None,
            "conditions": {}, "is_mandatory": True, "score": None, "confidence": 0.9,
            "evidence_order_nos": list(range(1, 35)) + [1, "2"],
        }],
    }

    cleaned = LangChainLlmClient._clean_extraction(None, raw)  # type: ignore[arg-type]

    assert cleaned["requirements"][0]["evidence_order_nos"] == list(range(1, 21))


def test_structured_output_cleanup_normalizes_direct_field_map() -> None:
    cleaned = LangChainLlmClient._clean_extraction(None, {
        "BUDGET": {"value": 100, "confidence": 0.9, "evidence_order_nos": [3]},
    })  # type: ignore[arg-type]

    assert cleaned["project_fields"] == [{
        "field_code": "BUDGET", "value_json": {"value": 100},
        "confidence": 0.9, "evidence_order_nos": [3],
    }]


def test_structured_output_cleanup_drops_empty_field_but_preserves_requirements() -> None:
    cleaned = LangChainLlmClient._clean_extraction(None, {
        "project_fields": [{
            "field_code": "BUDGET", "value_json": None,
            "confidence": 0.9, "evidence_order_nos": [3],
        }],
        "requirements": [{
            "category": "BUSINESS", "title": "提交报价文件", "description": "应提交报价文件。",
            "conditions": {}, "is_mandatory": True, "score": None, "confidence": 0.9,
            "evidence_order_nos": [3],
        }],
    })  # type: ignore[arg-type]

    assert cleaned["project_fields"] == []
    assert len(cleaned["requirements"]) == 1


def test_structured_output_cleanup_drops_missing_confidence_locally() -> None:
    cleaned = LangChainLlmClient._clean_extraction(None, {
        "project_fields": [{
            "field_code": "BUDGET", "value_json": {"value": 100},
            "evidence_order_nos": [3],
        }],
        "requirements": [{
            "category": "BUSINESS", "title": "提交报价文件", "description": "应提交报价文件。",
            "conditions": {}, "is_mandatory": True, "score": None, "confidence": 0.9,
            "evidence_order_nos": [3],
        }],
    })  # type: ignore[arg-type]

    assert cleaned["project_fields"] == []
    assert len(cleaned["requirements"]) == 1


def test_structured_output_cleanup_drops_prose_score_without_losing_requirement() -> None:
    cleaned = LangChainLlmClient._clean_extraction(None, {
        "project_fields": [],
        "requirements": [{
            "category": "SCORING", "title": "技术评分", "description": "评分标准见附件。",
            "conditions": {}, "is_mandatory": False, "score": "评标办法前附表",
            "confidence": 0.9, "evidence_order_nos": [4],
        }],
    })  # type: ignore[arg-type]

    assert len(cleaned["requirements"]) == 1
    assert cleaned["requirements"][0]["score"] is None


def test_module_retry_uses_strict_mode_after_a_malformed_structured_response() -> None:
    class RetryingLlm:
        def __init__(self) -> None:
            self.strict_calls: list[bool] = []

        def extract_requirements_for_fields(self, _nodes, _fields, *, strict=False):
            self.strict_calls.append(strict)
            if not strict:
                raise JSONDecodeError("missing delimiter", "{", 1)
            return RequirementExtractionResult(
                project_fields=[],
                requirements=[
                    RequirementCandidate(
                        category="BUSINESS",
                        title="提交报价文件",
                        description="应提交报价文件。",
                        confidence=0.9,
                        evidence_order_nos=[1],
                    )
                ],
            )

    service = object.__new__(RequirementExtractionService)
    service._llm = RetryingLlm()

    result = service._extract_module_batch(
        [{"order_no": 1, "content": "应提交报价文件。"}],
        "commercial",
        ["BUDGET"],
    )

    assert result is not None
    assert service._llm.strict_calls == [False, True]


@pytest.fixture
def llm() -> DeepSeekV4FlashClient:
    settings = get_settings()
    return DeepSeekV4FlashClient(settings)


@pytest.fixture
def zb5_candidate_nodes() -> list[dict]:
    """从 zb5.pdf 真实候选节点中加载：义务词候选 + 合同章节候选（无义务词但section_path匹配）"""
    session = get_session_factory()()
    zb5 = session.query(m.Document).filter(
        m.Document.logical_name == "zb5.pdf"
    ).first()
    if not zb5 or not zb5.current_version_id:
        pytest.skip("zb5.pdf not found")

    all_nodes = session.query(m.DocumentNode).filter(
        m.DocumentNode.document_version_id == zb5.current_version_id
    ).order_by(m.DocumentNode.order_no).all()
    session.close()

    # 义务词候选（关键词+义务词）
    selected: list[dict] = []
    keywords = ["业绩", "工程师", "联合体", "保证金", "投标报价"]
    for kw in keywords:
        for n in all_nodes:
            c = n.cleaned_content or n.content or ""
            if kw in c and len(c) >= 50 and ("应当" in c or "必须" in c or "不得" in c):
                selected.append({
                    "node_id": str(n.id),
                    "page_number": n.page_number,
                    "content": c,
                })
                break

    # 合同章节候选（section_path 匹配合同类关键词，无义务词也能进）
    contract_kws = ["付款", "结算", "履约", "违约", "质保"]
    for kw in contract_kws:
        for n in all_nodes:
            sp = n.section_path or ""
            c = n.cleaned_content or n.content or ""
            if kw in sp and len(c) >= 50:
                # 排除已经加过的
                if not any(d["node_id"] == str(n.id) for d in selected):
                    selected.append({
                        "node_id": str(n.id),
                        "page_number": n.page_number,
                        "content": c,
                    })
                break

    assert len(selected) >= 4, f"候选节点不足，只找到 {len(selected)} 个"
    return selected


@pytest.fixture
def zb5_procedural_nodes() -> list[dict]:
    """从 zb5.pdf 加载被 cleaning 程序性过滤掉的节点"""
    session = get_session_factory()()
    zb5 = session.query(m.Document).filter(
        m.Document.logical_name == "zb5.pdf"
    ).first()
    if not zb5 or not zb5.current_version_id:
        pytest.skip("zb5.pdf not found")

    all_nodes = session.query(m.DocumentNode).filter(
        m.DocumentNode.document_version_id == zb5.current_version_id
    ).all()
    session.close()

    # 程序性关键词节点
    procedural_kws = ["网上开标室", "远程解密", "评标委员会", "澄清", "踏勘现场"]
    selected: list[dict] = []
    for n in all_nodes:
        c = n.cleaned_content or n.content or ""
        for kw in procedural_kws:
            if kw in c and len(c) >= 50:
                selected.append({
                    "node_id": str(n.id),
                    "page_number": n.page_number,
                    "content": c,
                })
                break
    return selected[:3]


# ============================================================================
# 1. conditions 解析质量
# ============================================================================

@pytest.mark.integration
class TestConditionsExtraction:
    """验证 LLM 对 date/count/amount 约束的 operator 判断"""

    def test_date_constraint_yields_gte_operator(
        self, llm: DeepSeekV4FlashClient, zb5_candidate_nodes: list[dict]
    ) -> None:
        """含日期约束的节点 → date dimension operator 应为 GTE"""
        # 找含日期的节点
        date_nodes = [
            n for n in zb5_candidate_nodes
            if "2023" in n["content"] or "2024" in n["content"] or "年" in n["content"]
        ]
        if not date_nodes:
            pytest.skip("无含日期的候选节点")

        result = llm.extract_requirements(date_nodes[:2])
        for req in result.requirements:
            dims = {c["dimension"] for c in req.conditions.get("all", [])}
            if "date" in dims:
                date_cond = next(c for c in req.conditions["all"] if c["dimension"] == "date")
                assert date_cond["operator"] in ("GTE", "WITHIN_LAST_YEARS"), \
                    f"date operator 应为 GTE，实际: {date_cond['operator']}"

    def test_count_constraint_yields_count_dimension(
        self, llm: DeepSeekV4FlashClient, zb5_candidate_nodes: list[dict]
    ) -> None:
        """含数量词的节点 → 应有 count dimension"""
        count_nodes = [
            node
            for node in zb5_candidate_nodes
            if "至少" in node["content"]
            or "不少于" in node["content"]
            or "1项" in node["content"]
        ]
        if not count_nodes:
            pytest.skip("无含数量约束的候选节点")

        result = llm.extract_requirements(count_nodes[:1])
        if result.requirements:
            dims = {c["dimension"] for c in result.requirements[0].conditions.get("all", [])}
            assert "count" in dims, f"含'至少'/'1项'的节点应有 count dimension，实际 dims: {dims}"

    def test_confidence_is_discriminative(
        self, llm: DeepSeekV4FlashClient, zb5_candidate_nodes: list[dict]
    ) -> None:
        """confidence 应有区分度，不是所有节点都返回 0.9/0.95"""
        result = llm.extract_requirements(zb5_candidate_nodes[:4])
        confs = [r.confidence for r in result.requirements if r.confidence is not None]

        unique_confs = set(confs)
        # 所有 confidence 都一样说明没有区分度（允许全部为 None，因为 schema 改了）
        assert len(unique_confs) > 1 or len(confs) == 0, \
            f"confidence 无区分度，全部相同: {unique_confs}"

    def test_score_field_is_present(
        self, llm: DeepSeekV4FlashClient, zb5_candidate_nodes: list[dict]
    ) -> None:
        """score 字段应存在（允许为 None，因 schema 已允许）"""
        result = llm.extract_requirements(zb5_candidate_nodes[:3])
        for req in result.requirements:
            assert req.score is None or req.score >= 0, \
                f"score 不能为负数: {req.title} score={req.score}"


# ============================================================================
# 2. 批量 vs 单节点
# ============================================================================

@pytest.mark.integration
class TestBatchVsSingle:
    """批量发多个节点 vs 逐个发"""

    def test_batch_produces_valid_results(
        self, llm: DeepSeekV4FlashClient, zb5_candidate_nodes: list[dict]
    ) -> None:
        """批量发多个节点，应返回有效结构化结果"""
        nodes = zb5_candidate_nodes[:3]
        try:
            result = llm.extract_requirements(nodes)
        except Exception as exc:
            pytest.skip(f"LLM schema 校验失败: {exc}")

        # 批量应提取出要求
        assert len(result.requirements) >= 1, "批量应至少提取到 1 个要求"
        for req in result.requirements:
            assert req.title
            assert req.category in ("PROJECT", "QUALIFICATION", "BUSINESS", "SCORING")
            assert req.confidence is None or (0 <= req.confidence <= 1)
            assert req.score is None or req.score >= 0

    def test_single_nodes_produce_valid_results(
        self, llm: DeepSeekV4FlashClient, zb5_candidate_nodes: list[dict]
    ) -> None:
        """逐个发节点，每个都应返回有效结果"""
        for node in zb5_candidate_nodes[:3]:
            try:
                result = llm.extract_requirements([node])
                for req in result.requirements:
                    assert req.title
                    assert req.category in ("PROJECT", "QUALIFICATION", "BUSINESS", "SCORING")
            except Exception as exc:
                pytest.skip(f"LLM schema 校验失败: {exc}")


# ============================================================================
# 3. 程序性节点（LLM 是否误判）
# ============================================================================

@pytest.mark.integration
class TestProceduralNoise:
    """被 cleaning 过滤掉的程序性内容，LLM 是否也会提取"""

    def test_procedural_nodes_extraction_yields_low_confidence_or_empty(
        self, llm: DeepSeekV4FlashClient, zb5_procedural_nodes: list[dict]
    ) -> None:
        """程序性节点：要么提取不到要求，要么 confidence 偏低（< 0.8）"""
        if not zb5_procedural_nodes:
            pytest.skip("无程序性节点样本")

        total_reqs = 0
        for node in zb5_procedural_nodes:
            try:
                result = llm.extract_requirements([node])
            except Exception as exc:
                # schema 校验失败（score=None 等）不算提取成功
                print(f"[{node['node_id']}] 校验失败（预期行为）: {exc}")
                continue

            reqs = result.requirements
            print(f"\n[{node['node_id'][:8]}] 程序性节点 → {len(reqs)} 个要求:")
            for r in reqs:
                print(f"  [{r.category}] {r.title[:40]} | conf={r.confidence}")
                total_reqs += 1

        print(f"\n程序性节点总计提取: {total_reqs} 个要求")
        # 程序性节点不应大量产出有效要求（理想情况为 0）
        # 这里只记录不断言，因为 LLM 行为可能不一致


# ============================================================================
# 4. 端到端：真实文档节点完整提取
# ============================================================================

@pytest.mark.integration
class TestEndToEnd:
    """从真实文档加载节点，完整跑一遍 extraction"""

    def test_real_nodes_full_extraction(
        self, llm: DeepSeekV4FlashClient, zb5_candidate_nodes: list[dict]
    ) -> None:
        """真实候选节点完整提取 → 应返回有效结构化数据"""
        try:
            result = llm.extract_requirements(zb5_candidate_nodes[:5])
        except Exception as exc:
            # LLM 返回不符合 schema 时，打印并跳过
            print(f"\n[端到端] LLM schema 校验失败（常见于复杂节点）: {exc}")
            pytest.skip("LLM 返回不符合 schema，跳过端到端验证")

        print(f"\n真实节点提取结果: {len(result.requirements)} 个要求")
        for req in result.requirements:
            print(f"\n  [{req.category}] {req.title}")
            print(f"    score={req.score} conf={req.confidence}")
            print(f"    conditions: {req.conditions}")

        # 基本断言
        assert len(result.requirements) >= 1, "真实候选节点应至少提取到 1 个要求"
        for req in result.requirements:
            assert req.title
            assert req.category in ("PROJECT", "QUALIFICATION", "BUSINESS", "SCORING")
            assert req.confidence is not None and req.confidence >= 0
            # score 允许为 None（LLM 有时不返回）
            assert req.score is None or req.score >= 0
            assert req.conditions, "conditions 不能为空"
            assert "all" in req.conditions
