"""完整分析的 ARQ 入口。"""

from uuid import UUID

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.modules.analysis.full_analysis_service import FullAnalysisService


async def run_full_project_analysis(_: dict[object, object], run_id: str) -> dict[str, str]:
    async with get_session_factory()() as session:
        await FullAnalysisService(session, get_settings()).process(UUID(run_id))
    return {"run_id": run_id, "status": "processed"}
