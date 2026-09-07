"""Evidence 索引的 ARQ 任务函数。"""

from uuid import UUID

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.modules.indexing.service import EvidenceIndexingService


async def index_project_evidences(_: dict[object, object], job_id: str) -> dict[str, str]:
    """Worker 只接收索引任务 ID，项目范围和文本始终从 PostgreSQL 回查。"""
    async with get_session_factory()() as session:
        await EvidenceIndexingService(session, get_settings()).process(UUID(job_id))
    return {"job_id": job_id, "status": "processed"}
