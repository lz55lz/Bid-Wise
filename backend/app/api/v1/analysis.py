"""异步项目发现项分析 HTTP 接口。"""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.modules.analysis.schemas import FindingAnalysisJobResponse
from app.modules.analysis.service import FindingAnalysisService

router = APIRouter(prefix="/projects/{project_id}/finding-analysis", tags=["AI 发现项分析"])


@router.post("", response_model=FindingAnalysisJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_finding_analysis(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> FindingAnalysisJobResponse:
    """冻结当前 Evidence 后提交 MiniMax-M3 分析任务。"""
    return await FindingAnalysisService(session, settings).submit(project_id, current_user)


@router.get("/{job_id}", response_model=FindingAnalysisJobResponse)
async def get_finding_analysis(
    project_id: UUID,
    job_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> FindingAnalysisJobResponse:
    """查询任务状态；成员校验和项目归属校验均在服务端执行。"""
    return await FindingAnalysisService(session, settings).get(project_id, job_id, current_user)
