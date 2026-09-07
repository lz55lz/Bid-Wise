from uuid import UUID

from fastapi import APIRouter

from app.api.deps import CurrentUser, DatabaseSession
from app.core.errors import DomainError
from app.modules.decisions.service import DecisionService
from app.modules.projects.service import ProjectService

router = APIRouter(prefix="/projects/{project_id}/decision", tags=["投标决策"])


def _response(item) -> dict[str, object]:
    return {
        "id": str(item.id),
        "decision": item.decision,
        "score": item.score,
        "summary": item.summary,
        "input_snapshot": item.input_snapshot,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.post("/generate")
async def generate(
    project_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> dict[str, object]:
    await ProjectService(session).require_document_write(project_id, current_user)
    item = await DecisionService(session).generate(project_id)
    return _response(item)


@router.get("")
async def get_current_decision(
    project_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> dict[str, object]:
    await ProjectService(session).require_project_access(project_id, current_user)
    item = await DecisionService(session).get(project_id)
    if item is None:
        raise DomainError("RESOURCE_NOT_FOUND", "尚未生成投标决策", 404)
    return _response(item)
