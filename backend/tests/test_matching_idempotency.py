from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from app.services.matching_service import MatchingService


def test_missing_material_pair_is_reactivated_on_a_new_analysis_run() -> None:
    service = object.__new__(MatchingService)
    service._session = Mock()
    service._matches = Mock()
    requirement_id = UUID("12345678-1234-5678-1234-567812345678")
    prior = SimpleNamespace(
        id=UUID("87654321-4321-8765-4321-876543218765"),
        automatic_status="MISSING",
        final_status="MISSING",
        reason="previous run",
        is_current=False,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    service._matches.find_pair.return_value = prior
    service._matches.list_evidence_links.return_value = []

    MatchingService._upsert_match_result(
        service,
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        SimpleNamespace(id=requirement_id),
        None,
        "MISSING",
        "No compatible confirmed enterprise material.",
        [],
    )

    service._matches.add.assert_not_called()
    assert prior.is_current is True
    assert prior.automatic_status == "MISSING"
    assert prior.final_status == "MISSING"
    assert prior.reason == "No compatible confirmed enterprise material."
