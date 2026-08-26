from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db.models import DocumentNode
from app.services.document_cleaning_service import (
    CleaningQualityRejected,
    DocumentCleaningService,
)


def _node(order_no: int, content: str, node_type: str = "PARAGRAPH", section_path: str | None = None) -> DocumentNode:
    return DocumentNode(
        id=uuid4(),
        document_version_id=uuid4(),
        parent_node_id=None,
        node_type=node_type,
        page_number=order_no,
        section_path=section_path,
        order_no=order_no,
        content=content,
        content_hash="0" * 64,
        cleaned_content=None,
        cleaning_metadata={},
        bbox=None,
        metadata_={},
        created_at=datetime.now(UTC),
    )


def test_cleaning_preserves_raw_text_and_excludes_artifacts_and_duplicates() -> None:
    raw = "第一条　申请人应当具有独立承担民事责任的能力。"
    first = _node(1, raw)
    page_number = _node(2, "第 1 页")
    duplicate = _node(3, raw)

    outcome = object.__new__(DocumentCleaningService)._clean([first, page_number, duplicate])

    assert first.content == raw
    assert first.cleaned_content == "第一条 申请人应当具有独立承担民事责任的能力。"
    assert first.cleaning_metadata["indexable"] is True
    assert page_number.cleaning_metadata["indexable"] is False
    assert "PAGE_ARTIFACT" in page_number.cleaning_metadata["flags"]
    assert duplicate.cleaning_metadata["indexable"] is False
    assert "DUPLICATE_FRAGMENT" in duplicate.cleaning_metadata["flags"]
    assert outcome.summary["indexable_nodes"] == 1


def test_cleaning_rejects_a_document_without_usable_content() -> None:
    with pytest.raises(CleaningQualityRejected):
        object.__new__(DocumentCleaningService)._clean([_node(1, "第 1 页"), _node(2, "2")])


def test_cleaning_rejects_mojibake_even_when_it_uses_unicode_letters() -> None:
    node = _node(1, "αβγδεζηθικλμνξοπρστυφχψω")

    with pytest.raises(CleaningQualityRejected):
        object.__new__(DocumentCleaningService)._clean([node])

    assert node.cleaning_metadata["indexable"] is False
    assert "GARBLED_TEXT" in node.cleaning_metadata["flags"]


# ===========================================================================
# 义务词过滤测试（tender_req_candidate 标签）
# ===========================================================================


def test_tender_req_candidate_requires_obligation_word_in_paragraph() -> None:
    """PARAGRAPH：含关键词但不含义务词 → 不应成为候选"""
    # 关键词 "投标保证金" 但没有义务词，是描述性正文
    content = "投标保证金金额为项目预算的2%，通过银行转账方式缴纳。"
    node = _node(1, content)

    outcome = object.__new__(DocumentCleaningService)._clean([node])

    # 内容通过 indexable，但不含义务词，tender_req_candidate 应为 False
    assert node.cleaning_metadata["indexable"] is True
    assert node.tender_req_candidate is False


def test_tender_req_candidate_passes_with_obligation_word() -> None:
    """PARAGRAPH：含关键词 + 含义务词 + >=50 字符 → 应成为候选"""
    content = "投标人应当在投标截止日前按照招标文件规定的格式提交投标保证金，保证金金额为项目预算的2%，通过银行转账方式缴纳。"
    assert len(content) >= 50
    node = _node(1, content)

    outcome = object.__new__(DocumentCleaningService)._clean([node])

    assert node.cleaning_metadata["indexable"] is True
    assert node.tender_req_candidate is True


def test_tender_req_candidate_short_content_rejected() -> None:
    """内容 < 50 字符，即使含义务词和关键词，也不应成为候选"""
    content = "应当提供资质证书。投标人应当提供。"
    assert len(content) < 50
    node = _node(1, content)

    outcome = object.__new__(DocumentCleaningService)._clean([node])

    assert node.tender_req_candidate is False


def test_tender_req_candidate_section_path_bypasses_obligation_check() -> None:
    """SECTION 节点：section_path 匹配关键词即成为候选，不要求义务词"""
    content = "3.1 投标文件要求"
    section_path = "第三章 投标要求 / 3.1 投标文件要求"
    node = _node(1, content, node_type="SECTION", section_path=section_path)

    outcome = object.__new__(DocumentCleaningService)._clean([node])

    assert node.cleaning_metadata["indexable"] is True
    assert node.tender_req_candidate is True


def test_tender_req_candidate_negative_pattern_rejected() -> None:
    """目录/附录类节点应被排除（与有效节点混合，确保质量通过）"""
    # 混入有效节点以确保质量分数足够
    valid_node = _node(1, "投标人应当在投标截止日前按照招标文件规定的格式提交投标保证金，保证金金额为项目预算的2%，通过银行转账方式缴纳。")
    node2 = _node(2, "目 录")

    outcome = object.__new__(DocumentCleaningService)._clean([valid_node, node2])

    assert valid_node.cleaning_metadata["indexable"] is True
    assert valid_node.tender_req_candidate is True
    assert node2.tender_req_candidate is False


def test_tender_req_candidate_mixed_quality() -> None:
    """混合场景：多个节点，质量各异"""
    nodes = [
        _node(1, "投标人应当在投标截止日前按照招标文件规定的格式缴纳投标保证金，保证金金额为项目预算的2%，通过银行转账方式缴纳，逾期视为自动放弃。"),  # 关键词(投标保证金)+义务词 → 候选
        _node(2, "投标保证金金额为项目预算的2%，通过银行转账方式缴纳。"),  # 含投标保证金但无义务词 → 不候选
        _node(3, "本项目位于北京市海淀区，总建筑面积约20000平方米。"),  # 无关键词 → 不候选
        _node(4, "附录一：资格预审要求"),  # 噪音 → 不候选
    ]

    object.__new__(DocumentCleaningService)._clean(nodes)

    assert nodes[0].tender_req_candidate is True
    assert nodes[1].tender_req_candidate is False
    assert nodes[2].tender_req_candidate is False
    assert nodes[3].tender_req_candidate is False


def test_tender_req_candidate_contract_section_bypasses_obligation() -> None:
    """合同类章节（付款/结算/履约）的正文即使无义务词也应成为候选"""
    content = "委托人在合同约定的期限内未向监理人支付到期应付款项的，应就逾期付款额按同期贷款市场报价利率向监理人计付违约金。"
    assert len(content) >= 50
    node = _node(1, content, section_path="第八章 合同条款 / 8.2 付款与结算")

    outcome = object.__new__(DocumentCleaningService)._clean([node])

    assert node.cleaning_metadata["indexable"] is True
    assert node.tender_req_candidate is True  # section_path 含"合同条款"和"付款"，应 bypass


def test_tender_req_candidate_procedural_content_rejected() -> None:
    """含义务词+关键词，但属于程序性内容（开标/解密流程）→ 不候选"""
    # 有义务词"须"，有关键词"投标"，但讲的是网上开标解密步骤
    content = "投标人须在投标截止时间前登录云南省公共资源交易信息网网上开标室，完成远程解密操作，逾期视为自动放弃投标资格。"
    assert len(content) >= 50
    node = _node(1, content)

    outcome = object.__new__(DocumentCleaningService)._clean([node])

    assert node.cleaning_metadata["indexable"] is True
    assert node.tender_req_candidate is False  # 程序性内容不应成为候选
