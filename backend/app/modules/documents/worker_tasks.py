"""文档域 ARQ 任务函数。"""

from uuid import UUID

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.modules.documents.parsing_service import DocumentParsingService


async def parse_project_document(_: dict[object, object], job_id: str) -> dict[str, str]:
    """Worker 只接收数据库任务 ID，版本与对象归属均在服务内回查。"""
    async with get_session_factory()() as session:
        await DocumentParsingService(session, get_settings()).process(UUID(job_id))
    return {"job_id": job_id, "status": "processed"}
