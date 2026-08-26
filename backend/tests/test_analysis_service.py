from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from app.services import analysis_service
from app.services.analysis_service import AnalysisService
from app.core.errors import DomainError


@pytest.mark.parametrize(
    ("succeeded", "error_message", "expected_status"),
    [
        (True, None, "SUCCEEDED"),
        (False, "Report output failed.", "FAILED"),
    ],
)
def test_report_terminal_state_updates_analysis_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    succeeded: bool,
    error_message: str | None,
    expected_status: str,
) -> None:
    report_id = UUID("12345678-1234-5678-1234-567812345678")
    run_id = UUID("87654321-4321-8765-4321-876543218765")
    session = Mock()
    report = SimpleNamespace(analysis_run_id=run_id)
    run = SimpleNamespace(
        id=run_id,
        status="RUNNING",
        current_stage="REPORT_GENERATING",
        completed_at=None,
        error_code=None,
        error_message=None,
    )
    snapshot = SimpleNamespace(
        stage_outputs={"REPORT": {"status": "QUEUED", "task_id": "task-1"}}
    )
    repository = Mock()
    repository.get_run.return_value = run
    repository.get_snapshot.return_value = snapshot

    session.get.return_value = report
    monkeypatch.setattr(analysis_service, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(analysis_service, "AnalysisRepository", lambda _: repository)

    AnalysisService.mark_report_terminal(
        report_id, succeeded=succeeded, error_message=error_message
    )

    assert run.status == ("SUCCEEDED" if succeeded else "FAILED")
    assert snapshot.stage_outputs["REPORT"]["status"] == expected_status
    assert snapshot.stage_outputs["REPORT"]["report_id"] == str(report_id)
    assert snapshot.stage_outputs["REPORT"]["error_message"] == error_message
    assert "completed_at" in snapshot.stage_outputs["REPORT"]
    session.commit.assert_called_once_with()
    session.close.assert_called_once_with()


def test_submit_rejects_analysis_when_high_priority_requirements_are_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Mock()
    service = AnalysisService(session)
    service._projects = Mock()
    service._projects.get_visible.return_value = SimpleNamespace(status="ACTIVE")
    service._requirements = Mock()
    service._requirements.has_pending_for_project.return_value = True
    service._runs = Mock()
    monkeypatch.setattr(analysis_service, "can_write_project_documents", lambda _: True)

    with pytest.raises(DomainError) as exc_info:
        service.submit(
            UUID("12345678-1234-5678-1234-567812345678"),
            UUID("87654321-4321-8765-4321-876543218765"),
            {"BID_SPECIALIST"},
            Mock(),
        )

    assert exc_info.value.code == "ANALYSIS_REVIEW_PENDING"
    service._runs.build_input_manifest.assert_not_called()
