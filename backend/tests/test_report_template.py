from uuid import UUID

from app.modules.reports.schemas import ReportCitation
from app.modules.reports.service import ProjectReportService


def test_fixed_report_template_hides_internal_codes_and_uses_compact_sections() -> None:
    citation = ReportCitation(
        evidence_id=UUID("11111111-1111-1111-1111-111111111111"),
        quoted_text="投标人应具备法人资格。",
        locator={"document_name": "招标文件.pdf", "page_start": 13},
    )
    snapshot = {
        "project": {
            "name": "测试项目",
            "code": "T-01",
            "purchaser": "测试招标人",
            "bid_deadline": None,
        },
        "enterprises": [{"name": "测试企业", "is_lead": True}],
        "evidence": [
            {
                "evidence_id": str(citation.evidence_id),
                "locator": citation.locator,
                "content": citation.quoted_text,
            }
        ],
        "qualification_requirements": [
            {
                "requirement_id": "r1",
                "title": "法人资格",
                "is_mandatory": True,
                "evidence_ids": [str(citation.evidence_id)],
            }
        ],
        "matches": [
            {
                "requirement_id": "r1",
                "status": "待补充核验",
                "material_name": None,
                "reason": "材料信息不足",
            }
        ],
        "risks": [
            {
                "title": "资格材料待补充",
                "severity": "HIGH",
                "status": "OPEN",
                "description": "需要补充核验",
            }
        ],
        "decision": {"value": "NO_BID", "score": 50, "summary": "存在关键待核验项"},
    }

    content = ProjectReportService._replace_internal_labels(
        ProjectReportService._render_fixed_report(
            snapshot, "优先完成资格材料核验后再决策。", [citation]
        )
    )

    assert "UNCERTAIN" not in content
    assert "HIGH" not in content
    assert "OPEN" not in content
    assert "绑定企业投标适配度分析报告" in content
    assert "企业适配度结论" in content
    assert "绑定企业与材料概览" in content
    assert "企业与招标要求适配明细" in content
    assert "决定性缺口与推进条件" in content
    assert "匹配企业：未匹配到企业材料" in content
    assert "待补条件：无" in content
    assert "原文依据" in content
    assert "风险与待办" in content
    assert "投标人应具备法人资格" in content
    assert "关键招标要求" not in content
    assert "覆盖率" not in content


def test_report_sections_do_not_repeat_their_display_title() -> None:
    sections = ProjectReportService._sections(
        "# 投标准备检查单\n\n## 制作与递交原文补充\n\n核对签字盖章。"
    )

    submission = next(item for item in sections if item["title"] == "制作与递交原文补充")
    assert submission["content_markdown"] == "核对签字盖章。"
