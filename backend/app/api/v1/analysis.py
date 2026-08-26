"""Bid analysis run management API.

Submit bid analysis tasks (requirement extraction + qualification matching + risk check)
and query analysis history for a project.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db_session
from app.integrations.task_publisher import ArqTaskPublisher
from app.schemas.analysis import AnalysisRunResponse
from app.schemas.documents import TaskResponse
from app.services.analysis_service import AnalysisService

router = APIRouter(tags=["analysis-runs"])


@router.post(
    "/projects/{project_id}/analysis-runs",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_analysis_run(
    # Submit a full bid analysis run (extract requirements, match qualifications, assess risks).
    # Returns async task ID for status polling.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    return AnalysisService(session).submit(
        project_id, current_user.id, current_user.role_codes, ArqTaskPublisher()
    )


@router.get("/projects/{project_id}/analysis-runs", response_model=list[AnalysisRunResponse])
def list_analysis_runs(
    # List all analysis runs for a project (newest first).
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[AnalysisRunResponse]:
    return AnalysisService(session).list(project_id, current_user.id, current_user.role_codes)


@router.get("/analysis-runs/{run_id}", response_model=AnalysisRunResponse)
def get_analysis_run(
    # Get a specific analysis run by ID.
    run_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> AnalysisRunResponse:
    return AnalysisService(session).get(run_id, current_user.id, current_user.role_codes)
