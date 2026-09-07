from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.memories.service import MemoryService

router = APIRouter(prefix="/memories", tags=["长期记忆"])


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    project_id: UUID | None = None


@router.get("")
async def list_memories(
    current_user: CurrentUser, session: DatabaseSession
) -> list[dict[str, object]]:
    return await MemoryService(session).list(current_user)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreateRequest, current_user: CurrentUser, session: DatabaseSession
) -> dict[str, object]:
    return await MemoryService(session).create(current_user, payload.content, payload.project_id)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> Response:
    await MemoryService(session).delete(current_user, memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
