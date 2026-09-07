# ruff: noqa: E501
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.matching.service import MatchingService
from app.modules.projects.service import ProjectService

router = APIRouter(prefix="/projects/{project_id}/matches", tags=["材料匹配"])


class MatchOverrideRequest(BaseModel):
    final_status: str = Field(pattern="^(MATCHED|UNCERTAIN|MISSING)$")
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/run")
async def run_matches(
    project_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> dict[str, int]:
    await ProjectService(session).require_document_write(project_id, current_user)
    items = await MatchingService(session).run(project_id)
    return {"count": len(items)}


@router.get("")
async def list_matches(
    project_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> list[dict[str, object]]:
    await ProjectService(session).require_project_access(project_id, current_user)
    items = await MatchingService(session).list(project_id)
    return [
        {
            "id": str(item.id),
            "requirement_id": str(item.requirement_id),
            "material_id": None if item.material_id is None else str(item.material_id),
            "rule_status": item.rule_status,
            "final_status": item.final_status,
            "reason": item.reason,
        }
        for item in items
    ]


@router.patch("/{match_id}/override")
async def override_match(
    project_id: UUID,
    match_id: UUID,
    payload: MatchOverrideRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> dict[str, str]:
    await ProjectService(session).require_document_write(project_id, current_user)
    item = await MatchingService(session).override(
        project_id, match_id, current_user.id, payload.final_status, payload.reason
    )
    return {"id": str(item.id), "final_status": item.final_status}
