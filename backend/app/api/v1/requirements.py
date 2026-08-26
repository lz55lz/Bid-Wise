"""Bid requirement and project field management API.

CRUD for extracted bid requirements and project fields.
Requirements are LLM-extracted from tender documents; fields are structured key-value data.
Supports bulk review and per-item review operations.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db_session
from app.schemas.project_fields import ProjectFieldResponse, ProjectFieldReview
from app.schemas.requirements import RequirementBulkReview, RequirementResponse, RequirementReview
from app.services.project_field_service import ProjectFieldService
from app.services.requirement_service import RequirementService

router = APIRouter(tags=["requirements"])


@router.get("/projects/{project_id}/fields", response_model=list[ProjectFieldResponse])
def list_project_fields(
    # List all extracted project fields for a project (PROJECT_NAME, BUDGET, etc.).
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[ProjectFieldResponse]:
    return ProjectFieldService(session).list(project_id, current_user.id, current_user.role_codes)


@router.patch("/project-fields/{field_id}", response_model=ProjectFieldResponse)
def review_project_field(
    # Review/approve a project field (mark as correct or update value).
    field_id: UUID,
    payload: ProjectFieldReview,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ProjectFieldResponse:
    return ProjectFieldService(session).review(
        field_id, current_user.id, current_user.role_codes, payload
    )


@router.patch(
    "/projects/{project_id}/fields/{field_id}", response_model=ProjectFieldResponse
)
def review_project_scoped_field(
    # Review a field scoped to a specific project (project-level override).
    project_id: UUID,
    field_id: UUID,
    payload: ProjectFieldReview,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ProjectFieldResponse:
    return ProjectFieldService(session).review_in_project(
        project_id, field_id, current_user.id, current_user.role_codes, payload
    )


@router.get("/projects/{project_id}/requirements", response_model=list[RequirementResponse])
def list_requirements(
    # List all extracted bid requirements for a project.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[RequirementResponse]:
    return RequirementService(session).list(project_id, current_user.id, current_user.role_codes)


@router.patch(
    "/projects/{project_id}/requirements/bulk-review",
    response_model=list[RequirementResponse],
)
def bulk_review_project_requirements(
    # Bulk approve/reject multiple requirements at once.
    project_id: UUID,
    payload: RequirementBulkReview,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[RequirementResponse]:
    return RequirementService(session).bulk_review_in_project(
        project_id, current_user.id, current_user.role_codes, payload
    )


@router.patch("/requirements/{requirement_id}", response_model=RequirementResponse)
def review_requirement(
    # Review/approve a single requirement (mark as applicable, inapplicable, or update notes).
    requirement_id: UUID,
    payload: RequirementReview,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> RequirementResponse:
    return RequirementService(session).review(
        requirement_id, current_user.id, current_user.role_codes, payload
    )


@router.patch(
    "/projects/{project_id}/requirements/{requirement_id}", response_model=RequirementResponse
)
def review_project_requirement(
    # Review a requirement scoped to a specific project.
    project_id: UUID,
    requirement_id: UUID,
    payload: RequirementReview,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> RequirementResponse:
    return RequirementService(session).review_in_project(
        project_id, requirement_id, current_user.id, current_user.role_codes, payload
    )
