"""审计日志查询接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.identity.audit_query_service import AuditQueryService, response

router = APIRouter(prefix="/audit-logs", tags=["审计日志"])


@router.get("")
async def list_audit_logs(
    current_user: CurrentUser,
    session: DatabaseSession,
    project_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict[str, object]]:
    logs = await AuditQueryService(session).list(current_user, project_id, page, page_size)
    return [response(log) for log in logs]
