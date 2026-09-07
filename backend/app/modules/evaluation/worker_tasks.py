from uuid import UUID

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.modules.evaluation.service import EvaluationService


async def run_evaluation(_: dict[object, object], run_id: str) -> dict[str, str]:
    async with get_session_factory()() as session:
        await EvaluationService(session, get_settings()).process(UUID(run_id))
    return {"run_id": run_id, "status": "processed"}
