from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from app.services.enterprise_tag_matcher import EnterpriseTagMatcher


def _material(material_type: str, name: str, attributes: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        material_type=material_type,
        name=name,
        level=None,
        amount=None,
        attributes=attributes,
        valid_to=None,
    )


def _requirement(title: str, conditions: dict) -> SimpleNamespace:
    return SimpleNamespace(
        category="QUALIFICATION",
        title=title,
        description=title,
        conditions=conditions,
    )


def test_reverse_recall_matches_all_personnel_tag_conditions() -> None:
    requirement = _requirement(
        "项目经理资格要求",
        {
            "all": [
                {
                    "dimension": "建造师资格",
                    "operator": "CONTAINS_ONE_OF",
                    "value": "机电工程专业二级及以上注册建造师 / 通信与广电工程专业二级注册建造师",
                },
                {
                    "dimension": "安全生产考核合格证书",
                    "operator": "EQUALS",
                    "value": "持有安全生产考核合格证书",
                },
                {"dimension": "劳动关系", "operator": "EQUALS", "value": "有效"},
                {"dimension": "工作经验", "operator": "GTE", "value": "5年"},
                {
                    "dimension": "在建项目任职",
                    "operator": "EQUALS",
                    "value": "无在建项目任职",
                },
            ]
        },
    )
    material = _material(
        "PERSONNEL",
        "项目经理王工",
        {
            "建造师资格": "机电工程专业二级注册建造师",
            "安全生产考核合格证书": "持有",
            "劳动关系": "有效",
            "工作经验": 6,
            "在建项目任职": "无",
        },
    )
    matcher = EnterpriseTagMatcher()

    assert matcher.recall(requirement, [material]) == [material]
    evaluation = matcher.evaluate(requirement, material, date(2026, 9, 1))

    assert evaluation.status == "MATCHED"
    assert "建造师资格=机电工程专业二级注册建造师" in evaluation.reason
    assert evaluation.missing_conditions == []


def test_evidence_requirement_uses_related_tag_as_a_query_not_uploaded_file() -> None:
    requirement = _requirement(
        "投标人应提供近三年经审计的财务报表",
        {"all": [{"dimension": "evidence", "operator": "REQUIRED", "value": True}]},
    )
    material = _material(
        "QUALIFICATION",
        "2023-2025 年度审计财务报表",
        {"财务": "2023-2025年度审计财务报表", "审计": "第三方审计"},
    )
    matcher = EnterpriseTagMatcher()

    evaluation = matcher.evaluate(requirement, material, None)

    assert matcher.recall(requirement, [material]) == [material]
    assert evaluation.status == "MATCHED"
    assert "财务=" in evaluation.reason


def test_partial_tag_match_stays_uncertain_and_exposes_missing_dimension() -> None:
    requirement = _requirement(
        "项目经理资格要求",
        {
            "all": [
                {"dimension": "工作经验", "operator": "GTE", "value": "5年"},
                {
                    "dimension": "铁路营业线施工经验",
                    "operator": "EQUALS",
                    "value": "具备",
                },
            ]
        },
    )
    material = _material("PERSONNEL", "项目经理王工", {"工作经验": 6})

    evaluation = EnterpriseTagMatcher().evaluate(requirement, material, None)

    assert evaluation.status == "UNCERTAIN"
    assert evaluation.missing_conditions == [
        {"dimension": "铁路营业线施工经验", "operator": "EQUALS", "value": "具备"}
    ]


def test_extractor_style_codes_and_operators_match_registry_tags() -> None:
    requirement = _requirement(
        "项目经理资格要求",
        {
            "all": [
                {
                    "dimension": "registered_builder_qualification",
                    "operator": "in",
                    "value": "机电工程专业二级注册建造师;铁路工程专业一级注册建造师",
                },
                {
                    "dimension": "project_management_experience_years",
                    "operator": "gte",
                    "value": 5,
                },
            ]
        },
    )
    material = _material(
        "PERSONNEL",
        "项目经理王工",
        {"建造师资格": "机电工程专业二级注册建造师", "工作经验": 6},
    )

    evaluation = EnterpriseTagMatcher().evaluate(requirement, material, None)

    assert evaluation.status == "MATCHED"
    assert "建造师资格" in evaluation.reason
