"""知识库 ARQ 任务函数。"""

from uuid import UUID

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.modules.knowledge.parsing_service import KnowledgeParsingService


async def parse_knowledge_document(_: dict[object, object], job_id: str) -> dict[str, str]:
    async with get_session_factory()() as session:
        await KnowledgeParsingService(session, get_settings()).process(UUID(job_id))
    return {"job_id": job_id, "status": "processed"}
