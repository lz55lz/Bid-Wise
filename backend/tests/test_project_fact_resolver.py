from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from app.services.project_fact_resolver import (
    ProjectFactResolver,
    ResolvedBidDeadline,
    parse_bid_deadline,
)

PROJECT_ID = UUID("12345678-1234-5678-1234-567812345678")
FIELD_ID = UUID("87654321-4321-8765-4321-876543218765")
EVIDENCE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_parse_date_only_keeps_date_precision_and_does_not_expire_at_midnight() -> None:
    parsed = parse_bid_deadline("2026年8月25日")
    assert parsed is not None
    value, precision = parsed

    assert value == datetime(2026, 8, 25, tzinfo=UTC)
    assert precision == "DATE"

    # Date-only source values are not precise timestamps. They remain valid
    # through the specified calendar day rather than expiring at 00:00.
    result = ResolvedBidDeadline(
        value=value,
        precision=precision,
        source="PROJECT_FIELD",
    )
    assert result.is_expired(datetime(2026, 8, 25, 23, 59, tzinfo=UTC)) is False


def test_resolver_uses_confirmed_field_with_evidence_and_date_precision() -> None:
    resolver = ProjectFactResolver(Mock())
    resolver._fields = Mock()
    resolver._fields.list_for_project.return_value = [
        SimpleNamespace(
            id=FIELD_ID,
            field_code="bid_deadline",
            value_json={"value": "2026年8月25日"},
            confidence=Decimal("0.9500"),
            review_status="CONFIRMED",
            primary_evidence_id=EVIDENCE_ID,
            updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
    ]

    result = resolver.resolve_bid_deadline(SimpleNamespace(id=PROJECT_ID, bid_deadline=None))

    assert result.value == datetime(2026, 8, 25, tzinfo=UTC)
    assert result.precision == "DATE"
    assert result.source == "PROJECT_FIELD"
    assert result.evidence_ids == [EVIDENCE_ID]
    assert result.is_expired(datetime(2026, 8, 25, 23, 59, tzinfo=UTC)) is False
    assert result.is_expired(datetime(2026, 8, 26, tzinfo=UTC)) is True


def test_resolver_prioritizes_manual_datetime_over_extracted_field() -> None:
    resolver = ProjectFactResolver(Mock())
    resolver._fields = Mock()
    manual = datetime(2026, 8, 25, 9, 30, tzinfo=UTC)

    result = resolver.resolve_bid_deadline(
        SimpleNamespace(id=PROJECT_ID, bid_deadline=manual)
    )

    assert result.value == manual
    assert result.precision == "DATETIME"
    assert result.source == "PROJECT"
    resolver._fields.list_for_project.assert_not_called()


def test_parse_chinese_datetime_preserves_time_precision() -> None:
    assert parse_bid_deadline("2026年8月25日 下午3时05分") == (
        datetime(2026, 8, 25, 15, 5, tzinfo=UTC),
        "DATETIME",
    )
