"""Bid requirement vs enterprise qualification matching API.

Matches each bid requirement against enterprise qualification materials,
producing a deterministic, evidence-aware match result.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db_session
from app.integrations.task_publisher import ArqTaskPublisher
from app.schemas.documents import TaskResponse
from app.schemas.matches import MatchOverrideRequest, MatchResponse
from app.services.matching_service import MatchingService

router = APIRouter(tags=["matches"])


@router.post(
    "/projects/{project_id}/matches/run",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_matches(
    # Submit async matching task (ARQ background worker).
    # Returns task ID for status polling.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    return MatchingService(session).submit(
        project_id, current_user.id, current_user.role_codes, ArqTaskPublisher()
    )


@router.get("/projects/{project_id}/matches", response_model=list[MatchResponse])
def list_matches(
    # List all match results for a project.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[MatchResponse]:
    return MatchingService(session).list(project_id, current_user.id, current_user.role_codes)


@router.patch("/matches/{match_id}", response_model=MatchResponse)
def override_match(
    # Manually override a match result (e.g., mark as applicable despite low confidence).
    match_id: UUID,
    payload: MatchOverrideRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> MatchResponse:
    return MatchingService(session).override(
        match_id, current_user.id, current_user.role_codes, payload
    )


@router.patch("/projects/{project_id}/matches/{match_id}", response_model=MatchResponse)
def override_project_match(
    # Override a match result scoped to a specific project.
    project_id: UUID,
    match_id: UUID,
    payload: MatchOverrideRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> MatchResponse:
    return MatchingService(session).override_in_project(
        project_id, match_id, current_user.id, current_user.role_codes, payload
    )
