"""Bid report generation API.

Submit report generation tasks (simple/detailed) and download generated reports
in DOCX, PDF, or Markdown format. Reports summarize bid qualification results,
risks, and recommendations.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.config import get_settings
from app.db.session import get_db_session
from app.integrations.object_storage import MinioObjectStorage
from app.integrations.task_publisher import ArqTaskPublisher
from app.schemas.documents import TaskResponse
from app.schemas.reports import ReportResponse, ReportType
from app.services.report_service import ReportService

router = APIRouter(tags=["reports"])


def _service(session: Session) -> ReportService:
    return ReportService(session, MinioObjectStorage(get_settings()))


@router.post(
    "/projects/{project_id}/reports",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_report(
    # Submit async report generation task.
    # report_type: SIMPLE or DETAILED
    project_id: UUID,
    report_type: ReportType = "SIMPLE",
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    return _service(session).submit(
        project_id, current_user.id, current_user.role_codes, ArqTaskPublisher(),
        report_type=report_type,
    )


@router.post(
    "/projects/{project_id}/reports/generate",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
def generate_report_legacy(
    # Legacy endpoint: same as POST /projects/{project_id}/reports
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    return _service(session).submit(
        project_id, current_user.id, current_user.role_codes, ArqTaskPublisher()
    )


@router.get("/projects/{project_id}/reports", response_model=ReportResponse | None)
def get_latest_report(
    # Get the most recent report for a project (if any).
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ReportResponse | None:
    return _service(session).latest(project_id, current_user.id, current_user.role_codes)


@router.get("/reports/{report_id}", response_model=ReportResponse)
def get_report(
    # Get a specific report by ID.
    report_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ReportResponse:
    return _service(session).get(report_id, current_user.id, current_user.role_codes)


@router.get("/reports/{report_id}/download")
def download_report(
    # Download a generated report in DOCX, PDF, or MD format.
    report_id: UUID,
    report_format: str = Query(alias="format", pattern="^(docx|pdf|md)$"),
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> StreamingResponse:
    download = _service(session).create_authorized_download(
        report_id, report_format, current_user.id, current_user.role_codes
    )
    return StreamingResponse(
        download.stream,
        media_type=download.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{download.file_name}"'},
    )
