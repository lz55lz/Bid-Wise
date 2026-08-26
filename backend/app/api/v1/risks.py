"""Bid risk analysis API.

Submit risk analysis tasks and review risk flags for a bid project.
Risks are derived from bid requirements, enterprise profile, and qualification gaps.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db_session
from app.integrations.task_publisher import ArqTaskPublisher
from app.schemas.documents import TaskResponse
from app.schemas.risks import RiskResponse, RiskReviewRequest
from app.services.risk_service import RiskService

router = APIRouter(tags=["risks"])


@router.post(
    "/projects/{project_id}/risks/run",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_risk_check(
    # Submit async risk analysis task (ARQ background worker).
    # Returns task ID for status polling.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    return RiskService(session).submit(
        project_id, current_user.id, current_user.role_codes, ArqTaskPublisher()
    )


@router.get("/projects/{project_id}/risks", response_model=list[RiskResponse])
def list_risks(
    # List all identified risks for a project.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[RiskResponse]:
    return RiskService(session).list(project_id, current_user.id, current_user.role_codes)


@router.patch("/projects/{project_id}/risks/{risk_id}", response_model=RiskResponse)
def review_risk(
    # Mark a risk as reviewed/accepted or update its notes.
    project_id: UUID,
    risk_id: UUID,
    payload: RiskReviewRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> RiskResponse:
    return RiskService(session).review(
        project_id, risk_id, current_user.id, current_user.role_codes, payload
    )
