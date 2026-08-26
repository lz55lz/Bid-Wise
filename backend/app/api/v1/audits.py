"""Audit log query API.

Provides read-only access to system audit logs for tracking user actions
and system changes. Filterable by project.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db_session
from app.schemas.audit import AuditLogResponse
from app.services.audit_query_service import AuditQueryService

router = APIRouter(tags=["audits"])


@router.get("/audit-logs", response_model=list[AuditLogResponse])
def list_audit_logs(
    # Query audit logs for the current user.
    # Filter by project_id to narrow results to a specific project.
    project_id: UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[AuditLogResponse]:
    return AuditQueryService(session).list_for_actor(
        current_user.id, current_user.role_codes, project_id
    )
