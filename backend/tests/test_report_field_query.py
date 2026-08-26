from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from app.services.report_field_query import ReportFieldQueryContext, ReportFieldQueryService


def _field(*, code: str, value: object, evidence: UUID | None, confidence: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID("12345678-1234-5678-1234-567812345678"),
        field_code=code,
        value_json={"value": value},
        primary_evidence_id=evidence,
        confidence=Decimal(confidence),
        updated_at=datetime(2026, 8, 24, tzinfo=UTC),
        review_status="CONFIRMED",
    )


def test_query_context_reads_legacy_fields_and_returns_their_evidence() -> None:
    evidence_id = UUID("87654321-4321-8765-4321-876543218765")
    fields = ReportFieldQueryService._index_confirmed_fields([
        _field(code="bid_deadline", value="2026年9月1日", evidence=evidence_id, confidence="0.95")
    ])
    context = ReportFieldQueryContext(fields, [], [], [], [], {}, {}, {})

    assert context.value_for("BID_DEADLINE") == "2026年9月1日"
    assert context.evidence_for("BID_DEADLINE") == [evidence_id]


def test_query_context_excludes_confirmed_field_without_source_evidence() -> None:
    fields = ReportFieldQueryService._index_confirmed_fields([
        _field(code="BUDGET", value=100, evidence=None, confidence="0.95")
    ])

    assert fields == {}


def test_schedule_query_only_returns_confirmed_bidder_submission_requirements() -> None:
    schedule = SimpleNamespace(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        category="BUSINESS",
        title="投标文件递交截止时间",
        description="投标人应完成线上递交。",
        is_mandatory=True,
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    unrelated = SimpleNamespace(
        id=UUID("22222222-2222-2222-2222-222222222222"),
        category="BUSINESS",
        title="付款方式",
        description="合同付款方式。",
        is_mandatory=False,
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    alternative_bid = SimpleNamespace(
        id=UUID("33333333-3333-3333-3333-333333333333"),
        category="BUSINESS",
        title="不得递交备选投标方案",
        description="投标人不得递交备选投标方案。",
        is_mandatory=True,
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    context = ReportFieldQueryContext(
        {}, [unrelated, alternative_bid, schedule], [], [], [], {}, {}, {}
    )

    assert context.schedule_requirements() == [schedule]


def test_scoring_query_and_action_plan_use_confirmed_facts_only() -> None:
    scoring = SimpleNamespace(
        id=UUID("44444444-4444-4444-4444-444444444444"),
        category="SCORING",
        score=10,
        is_mandatory=False,
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    requirement = SimpleNamespace(
        id=UUID("55555555-5555-5555-5555-555555555555"),
        title="项目经理资格要求",
        description="投标人拟派项目经理应具有注册建造师资格",
        category="QUALIFICATION",
        conditions={"all": [{"dimension": "建造师资格", "operator": "EQUALS", "value": "具备"}]},
    )
    evidence = UUID("66666666-6666-6666-6666-666666666666")
    gap = SimpleNamespace(
        id=UUID("77777777-7777-7777-7777-777777777777"),
        requirement_id=requirement.id,
        final_status="MISSING",
        reason="企业标签未满足：建造师资格",
    )
    context = ReportFieldQueryContext(
        {}, [scoring], [requirement], [], [gap], {requirement.id: [evidence]}, {}, {}
    )

    assert context.scoring_requirements() == [scoring]
    assert context.action_plan_items()[0].priority == "P0"
    assert "1 项企业材料缺口" in context.action_plan_items()[0].action
    assert context.action_plan_items()[0].evidence_ids == [evidence]


def test_enterprise_gaps_exclude_misclassified_bid_review_clauses() -> None:
    requirement = SimpleNamespace(
        id=UUID("99999999-9999-9999-9999-999999999999"),
        title="3.1.2投标人有以下情形之一的，其投标作否决投标处理",
        description="评标委员会按投标文件规定进行初步评审。",
        category="SCORING",
        conditions={"all": [{"dimension": "evidence", "operator": "REQUIRED", "value": True}]},
    )
    gap = SimpleNamespace(
        id=UUID("88888888-8888-8888-8888-888888888888"),
        requirement_id=requirement.id,
        final_status="MISSING",
        reason="企业标签库中未召回可比较标签",
    )
    context = ReportFieldQueryContext({}, [], [requirement], [], [gap], {}, {}, {})

    assert context.enterprise_gaps() == []
