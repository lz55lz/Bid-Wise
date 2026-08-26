from app.services.quality_evaluation import (
    ProjectFieldCandidate,
    RequirementCandidate,
    evaluate_extraction_quality,
)


def test_evaluation_requires_page_category_mandatory_and_all_term_groups() -> None:
    golden = {
        "schema_version": 2,
        "requirements": [
            {
                "id": "manager",
                "page": 8,
                "category": "QUALIFICATION",
                "mandatory": True,
                "term_groups": [["项目经理"], ["注册建造师", "机电工程"]],
            }
        ],
        "project_fields": [
            {
                "id": "tenderer",
                "page": 1,
                "field_code": "tenderer",
                "terms": ["中国铁路"],
            }
        ],
        "thresholds": {
            "requirement_recall": 1.0,
            "mandatory_requirement_recall": 1.0,
            "requirement_evidence_location_rate": 1.0,
            "project_field_recall": 1.0,
        },
    }
    requirements = [
        RequirementCandidate(
            id="r-1",
            category="QUALIFICATION",
            content="项目经理须持有机电工程专业二级及以上注册建造师执业资格证书",
            mandatory=True,
            review_status="CONFIRMED",
            evidence_pages=frozenset({8}),
            source_evidence_count=1,
        )
    ]
    fields = [
        ProjectFieldCandidate(
            id="f-1",
            field_code="tenderer",
            value_text="中国铁路昆明局集团有限公司",
            review_status="CONFIRMED",
            evidence_page=1,
            source_evidence_count=1,
        )
    ]

    result = evaluate_extraction_quality(
        golden=golden, requirements=requirements, project_fields=fields
    )

    assert result["passed"] is True
    assert result["requirement_recall"] == 1.0
    assert result["mandatory_requirement_recall"] == 1.0
    assert result["project_field_recall"] == 1.0
    assert result["reviewed_precision"] == 1.0


def test_evaluation_fails_threshold_when_evidence_page_or_mandatory_is_wrong() -> None:
    golden = {
        "requirements": [
            {
                "id": "manager",
                "page": 8,
                "category": "QUALIFICATION",
                "mandatory": True,
                "terms": ["项目经理"],
            }
        ],
        "thresholds": {"requirement_recall": 1.0, "mandatory_requirement_recall": 1.0},
    }
    requirements = [
        RequirementCandidate(
            id="r-1",
            category="QUALIFICATION",
            content="项目经理资格要求",
            mandatory=False,
            review_status="PENDING",
            evidence_pages=frozenset({9}),
            source_evidence_count=1,
        )
    ]

    result = evaluate_extraction_quality(
        golden=golden, requirements=requirements, project_fields=[]
    )

    assert result["passed"] is False
    assert result["missed_requirement_ids"] == ["manager"]
    assert set(result["threshold_failures"]) == {
        "requirement_recall",
        "mandatory_requirement_recall",
    }


def test_v1_terms_default_to_any_for_backwards_compatible_golden_sets() -> None:
    result = evaluate_extraction_quality(
        golden={
            "requirements": [
                {
                    "id": "credit",
                    "page": 4,
                    "category": "QUALIFICATION",
                    "terms": ["失信", "黑名单"],
                }
            ]
        },
        requirements=[
            RequirementCandidate(
                id="r-1",
                category="QUALIFICATION",
                content="投标人不得列入失信被执行人名单",
                mandatory=True,
                review_status="PENDING",
                evidence_pages=frozenset({4}),
            )
        ],
        project_fields=[],
    )

    assert result["requirement_recall"] == 1.0
