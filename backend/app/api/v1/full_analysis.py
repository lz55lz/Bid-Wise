"""一键投标分析接口。"""

from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.core.errors import DomainError
from app.modules.analysis.full_analysis_service import FullAnalysisService
from app.modules.analysis.models import ProjectAnalysisRun
from app.modules.projects.service import ProjectService

router = APIRouter(prefix="/projects/{project_id}/full-analysis-runs", tags=["完整投标分析"])


def _response(item: ProjectAnalysisRun) -> dict[str, object]:
    return {
        "id": str(item.id),
        "project_id": str(item.project_id),
        "status": item.status,
        "current_stage": item.current_stage,
        "stage_outputs": item.stage_outputs,
        "error_code": item.error_code,
        "error_message": item.error_message,
        "created_at": item.created_at,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def submit(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> dict[str, object]:
    return _response(await FullAnalysisService(session, settings).submit(project_id, current_user))


@router.get("")
async def list_runs(
    project_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> list[dict[str, object]]:
    await ProjectService(session).require_project_access(project_id, current_user)
    rows = (
        await session.scalars(
            select(ProjectAnalysisRun)
            .where(ProjectAnalysisRun.project_id == project_id)
            .order_by(ProjectAnalysisRun.created_at.desc())
        )
    ).all()
    return [_response(row) for row in rows]


@router.get("/{run_id}")
async def get_run(
    project_id: UUID, run_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> dict[str, object]:
    await ProjectService(session).require_project_access(project_id, current_user)
    item = await session.get(ProjectAnalysisRun, run_id)
    if item is None or item.project_id != project_id:
        raise DomainError("ANALYSIS_RUN_NOT_FOUND", "完整分析运行不存在或无权访问", 404)
    return _response(item)
