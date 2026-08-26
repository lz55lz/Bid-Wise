"""Bid project management API.

CRUD for tender bid projects, including member management and project lifecycle
(archive, delete). Projects contain documents, requirements, matches, and reports.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.constants import SYSTEM_ADMIN
from app.core.permissions import can_manage_project
from app.db.session import get_db_session
from app.schemas.auth import AssignableUserResponse
from app.schemas.projects import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberResponse,
    ProjectResponse,
    ProjectUpdate,
)
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectResponse])
def list_projects(
    # List all projects the current user has access to (member or admin).
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[ProjectResponse]:
    return ProjectService(session).list_visible(current_user.id, current_user.role_codes)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    # Create a new bid project. Only SYSTEM_ADMIN or PROJECT_OWNER can create.
    payload: ProjectCreate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ProjectResponse:
    if (
        SYSTEM_ADMIN not in current_user.role_codes
        and "PROJECT_OWNER" not in current_user.role_codes
    ):
        from app.core.errors import DomainError

        raise DomainError("PERMISSION_DENIED", "Not authorized to create project", 403)
    return ProjectService(session).create(current_user.id, payload)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    # Get a single project by ID.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ProjectResponse:
    return ProjectService(session)._to_response(
        ProjectService(session).get_visible(project_id, current_user.id, current_user.role_codes)
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    # Update project metadata (name, deadline, enterprises, etc.).
    # Only project owner or system admin can update.
    project_id: UUID,
    payload: ProjectUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ProjectResponse:
    service = ProjectService(session)
    project = service.get_visible(project_id, current_user.id, current_user.role_codes)
    if not can_manage_project(current_user.role_codes, project.owner_id == current_user.id):
        from app.core.errors import DomainError

        raise DomainError("PERMISSION_DENIED", "Not authorized to modify project", 403)
    return service.update(
        project, current_user.id, payload, SYSTEM_ADMIN in current_user.role_codes
    )


@router.post("/{project_id}/archive", response_model=ProjectResponse)
def archive_project(
    # Archive a project (marks as read-only).
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ProjectResponse:
    service = ProjectService(session)
    project = service.get_visible(project_id, current_user.id, current_user.role_codes)
    if not can_manage_project(current_user.role_codes, project.owner_id == current_user.id):
        from app.core.errors import DomainError

        raise DomainError("PERMISSION_DENIED", "Not authorized to archive project", 403)
    return service.archive(project, current_user.id, SYSTEM_ADMIN in current_user.role_codes)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    # Permanently delete a project (soft delete).
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    service = ProjectService(session)
    project = service.get_visible(project_id, current_user.id, current_user.role_codes)
    if not can_manage_project(current_user.role_codes, project.owner_id == current_user.id):
        from app.core.errors import DomainError

        raise DomainError("PERMISSION_DENIED", "Not authorized to delete project", 403)
    service.delete(project, current_user.id, SYSTEM_ADMIN in current_user.role_codes)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{project_id}/members", status_code=status.HTTP_204_NO_CONTENT)
def add_member(
    # Add a user as project member with a specific role.
    project_id: UUID,
    payload: ProjectMemberCreate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    service = ProjectService(session)
    project = service.get_visible(project_id, current_user.id, current_user.role_codes)
    if not can_manage_project(current_user.role_codes, project.owner_id == current_user.id):
        from app.core.errors import DomainError

        raise DomainError("PERMISSION_DENIED", "Not authorized to manage members", 403)
    service.add_member(project, current_user.id, payload, SYSTEM_ADMIN in current_user.role_codes)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/members", response_model=list[ProjectMemberResponse])
def list_members(
    # List all members of a project.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[ProjectMemberResponse]:
    service = ProjectService(session)
    project = service.get_visible(project_id, current_user.id, current_user.role_codes)
    return service.list_members(project, current_user.id, SYSTEM_ADMIN in current_user.role_codes)


@router.get("/{project_id}/assignable-users", response_model=list[AssignableUserResponse])
def list_assignable_users(
    # List users that can be added as project members.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[AssignableUserResponse]:
    service = ProjectService(session)
    project = service.get_visible(project_id, current_user.id, current_user.role_codes)
    return [
        AssignableUserResponse.model_validate(user)
        for user in service.list_assignable_users(
            project, current_user.id, SYSTEM_ADMIN in current_user.role_codes
        )
    ]
