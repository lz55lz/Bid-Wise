"""
候选节点过滤验证测试类

用途：验证文档清洗服务的招标要求候选过滤逻辑是否正确
- 黑名单章节过滤（合同执行期规范不应进入候选）
- 义务词+关键词组合判断
- section 级候选上限
- 重复内容检测

运行方式：
    pytest tests/test_candidate_filter_verify.py -v
"""
from collections import Counter
from uuid import UUID

import pytest

from app.db import models as m
from app.db.session import get_session_factory
from app.services.document_cleaning_service import (
    _CONTRACT_SECTION_RE,
    _TENDER_OBLIGATION_RE,
    _TENDER_REQUIREMENT_CONTENT_RE,
    _TENDER_REQUIREMENT_SECTION_BLACKLIST_RE,
    DocumentCleaningService,
)

ZB8_VERSION_ID = UUID("2a8a45c0-5571-4629-83f2-cbf65c103ea2")


# ============================================================================
# 单元测试：黑名单章节过滤
# ============================================================================
class TestSectionBlacklist:
    """黑名单章节过滤验证：合同执行期章节不应进入候选"""

    @pytest.mark.parametrize(
        "section_path,should_be_blocked",
        [
            # 合同执行期章节（应被黑名单拦截）
            ("2.投标报价说明", True),
            ("1.工程技术规范", True),
            ("2.园林植物配置", True),
            ("4.乙方职责", True),
            ("第一章廉政协议", True),
            ("3.监理规则", True),
            ("2.投标报价说明", True),
            ("1.工程技术规范", True),
            ("2.园林植物配置", True),
            ("4.乙方职责", True),
            ("第一章廉政协议", True),
            ("3.监理规则", True),
            # 招标要求章节（应放行）
            ("1.投标人资格", False),
            ("2.投标保证金", False),
            ("3.合同条款", False),
            ("4.评标办法", False),
            ("2.投标有效期", False),
        ],
    )
    def test_blacklist_regex_matches(self, section_path: str, should_be_blocked: bool) -> None:
        """验证黑名单正则是否正确识别应拦截的章节"""
        matched = bool(_TENDER_REQUIREMENT_SECTION_BLACKLIST_RE.search(section_path))
        assert matched == should_be_blocked, (
            f"section_path='{section_path}' expected blocked={should_be_blocked}, got blocked={matched}"
        )


# ============================================================================
# 单元测试：义务词+关键词组合判断
# ============================================================================
class TestObligationKeywordFilter:
    """义务词+关键词组合判断验证"""

    @pytest.mark.parametrize(
        "content,section_path,node_type,expected",
        [
            # 义务词 + 关键词（且长度>=30）→ 应通过
            ("投标人应当具备有效的营业执照投标人资格，并在人员、设备、资金等方面具备相应的施工能力", "1.投标人资格", "PARAGRAPH", True),
            ("投标保证金不得超过招标控制价的2%，且须在投标截止时间前提交", "2.投标保证金", "PARAGRAPH", True),
            # 义务词 + 合同章节 bypass（需要>=50字）→ 应通过
            ("发包人应当按合同约定支付工程款，逾期付款需支付违约金，因发包人原因造成的一切损失由发包人承担", "3.合同条款", "PARAGRAPH", True),
            # 无义务词 + 关键词 → 应过滤
            ("本项目不接受联合体投标，投标人须独立参加本次招标活动", "1.投标人资格", "PARAGRAPH", False),
            # 黑名单章节 + 义务词 → 应过滤（即使长度够）
            ("投标人应当按照工程量清单格式填写报价，不得遗漏项目，否则视为无效投标", "2.投标报价说明", "PARAGRAPH", False),
            ("必须达到二级绿化养护标准，养护期满成活率应达90%以上，养护质量不达标需返工", "1.工程技术规范", "PARAGRAPH", False),
            # 内容太短（<30）且只有义务词 → 应过滤
            ("应当遵守规定", "1.投标人资格", "PARAGRAPH", False),
        ],
    )
    def test_is_tender_requirement_candidate(
        self, content: str, section_path: str, node_type: str, expected: bool
    ) -> None:
        """验证 _is_tender_requirement_candidate 判断逻辑"""
        svc = DocumentCleaningService.__new__(DocumentCleaningService)
        result = svc._is_tender_requirement_candidate(content, section_path, node_type)
        assert result == expected, (
            f"content='{content[:40]}' section='{section_path}' "
            f"expected={expected} got={result}"
        )


# ============================================================================
# 集成测试：清洗后候选节点统计
# ============================================================================
@pytest.mark.integration
class TestCandidateCleaning:
    """清洗流程集成验证：清洗后候选数应在合理范围"""

    @pytest.fixture
    def zb8_candidates(self) -> list[m.DocumentNode]:
        session = get_session_factory()()
        nodes = session.query(m.DocumentNode).filter(
            m.DocumentNode.document_version_id == ZB8_VERSION_ID,
            m.DocumentNode.tender_req_candidate == True,
        ).order_by(m.DocumentNode.order_no).all()
        session.close()
        return nodes

    def test_candidate_count_reasonable(self, zb8_candidates: list[m.DocumentNode]) -> None:
        """候选节点总数应在合理范围（20-100）"""
        count = len(zb8_candidates)
        assert 20 <= count <= 100, (
            f"候选数 {count} 不在合理范围 [20, 100]，"
            f"可能是过滤过严或过松"
        )

    def test_no_blacklist_sections(self, zb8_candidates: list[m.DocumentNode]) -> None:
        """候选中不应存在黑名单章节"""
        blacklist_terms = ["投标报价说明", "工程技术规范", "园林植物", "乙方职责"]
        violations = []
        for n in zb8_candidates:
            sec = n.section_path or ""
            for term in blacklist_terms:
                if term in sec:
                    violations.append(f"order={n.order_no} section={sec}")
        assert not violations, "候选中仍有黑名单章节:\n  " + "\n  ".join(violations)

    def test_section_density_warning(self, zb8_candidates: list[m.DocumentNode]) -> None:
        """单一 section 候选节点不宜过多（>5 提示检查）"""
        by_sec = Counter((n.section_path or "")[:80] for n in zb8_candidates)
        overloaded = [(s, c) for s, c in by_sec.items() if c > 5]
        if overloaded:
            print(f"\nWARNING: {len(overloaded)} 个 section 超过 5 个候选节点:")
            for s, c in overloaded[:5]:
                print(f"  [{c}] {s[:70]}")
        assert not overloaded, (
            f"发现 {len(overloaded)} 个 section 候选过多，可能导致重复提取"
        )

    def test_source_distribution(self, zb8_candidates: list[m.DocumentNode]) -> None:
        """候选来源分布验证：关键词+义务词应占主导"""
        source_dist = Counter()
        for n in zb8_candidates:
            content = n.cleaned_content or ""
            sec = n.section_path or ""
            has_obligation = bool(_TENDER_OBLIGATION_RE.search(content))
            has_keyword = bool(_TENDER_REQUIREMENT_CONTENT_RE.search(content))
            is_contract_bypass = bool(_CONTRACT_SECTION_RE.search(sec)) and len(content) >= 50

            if is_contract_bypass:
                source_dist["合同章节bypass"] += 1
            elif has_keyword and has_obligation:
                source_dist["关键词+义务词"] += 1
            elif has_keyword:
                source_dist["仅关键词"] += 1
            elif has_obligation:
                source_dist["仅义务词"] += 1
            else:
                source_dist["两者皆无"] += 1

        print(f"\n候选来源分布: {dict(source_dist.most_common())}")
        dominant = source_dist.most_common(1)[0][1]
        total = sum(source_dist.values())
        # 关键词+义务词应占主导（>40%）
        assert dominant / total > 0.4, (
            f"关键词+义务词占比 {dominant}/{total}={dominant/total:.1%} 过低，过滤逻辑可能过松"
        )
