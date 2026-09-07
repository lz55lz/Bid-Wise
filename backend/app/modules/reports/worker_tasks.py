"""项目报告 ARQ 任务函数。"""

from uuid import UUID

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.modules.reports.service import ProjectReportService


async def generate_project_report(_: dict[object, object], report_id: str) -> dict[str, str]:
    """Worker 只接收报告 ID，报告输入和项目归属从数据库回查。"""
    async with get_session_factory()() as session:
        await ProjectReportService(session, get_settings()).process(UUID(report_id))
    return {"report_id": report_id, "status": "processed"}
