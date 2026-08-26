"""Decision generation API.

Submit a decision generation task for a project and query results.
A decision summarizes bid qualification, risk assessment, and recommendation.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db_session
from app.integrations.task_publisher import ArqTaskPublisher
from app.schemas.decisions import DecisionResponse
from app.schemas.documents import TaskResponse
from app.services.decision_service import DecisionService

router = APIRouter(tags=["decisions"])


@router.post(
    "/projects/{project_id}/decision/generate",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_decision(
    # Submit decision generation task for a project.
    # Returns a task ID for polling status.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    return DecisionService(session).submit(
        project_id, current_user.id, current_user.role_codes, ArqTaskPublisher()
    )


@router.get("/projects/{project_id}/decision", response_model=DecisionResponse | None)
def get_latest_decision(
    # Get the most recent decision for a project (if any).
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> DecisionResponse | None:
    return DecisionService(session).latest(project_id, current_user.id, current_user.role_codes)
