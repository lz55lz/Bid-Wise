"""发现项分析的 ARQ 任务函数。"""

from uuid import UUID

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.modules.analysis.service import FindingAnalysisService


async def analyze_project_findings(_: dict[object, object], job_id: str) -> dict[str, str]:
    """Worker 只接收分析任务 ID，禁止从队列载荷读取项目或用户身份。"""
    async with get_session_factory()() as session:
        await FindingAnalysisService(session, get_settings()).process(UUID(job_id))
    return {"job_id": job_id, "status": "processed"}
