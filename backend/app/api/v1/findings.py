"""项目发现项与人工复核 HTTP 接口。"""

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.findings.schemas import FindingCreateRequest, FindingResponse, FindingReviewRequest
from app.modules.findings.service import FindingService

router = APIRouter(prefix="/projects/{project_id}/findings", tags=["项目发现项"])


@router.get("", response_model=list[FindingResponse])
async def list_findings(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[FindingResponse]:
    """列出当前用户已授权项目的发现项。"""
    return await FindingService(session).list_project(project_id, current_user, status_filter)


@router.post("", response_model=FindingResponse, status_code=status.HTTP_201_CREATED)
async def create_finding(
    project_id: UUID,
    payload: FindingCreateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> FindingResponse:
    """提交带 Evidence 的候选发现项，默认等待负责人复核。"""
    return await FindingService(session).create(project_id, current_user, payload)


@router.post("/{finding_id}/review", response_model=FindingResponse)
async def review_finding(
    project_id: UUID,
    finding_id: UUID,
    payload: FindingReviewRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> FindingResponse:
    """由项目负责人或系统管理员完成确认或驳回。"""
    return await FindingService(session).review(project_id, finding_id, current_user, payload)
