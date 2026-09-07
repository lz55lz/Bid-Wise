from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.identity.service import AuthenticatedUser
from app.modules.memories.models import UserMemory
from app.modules.memories.repository import MemoryRepository
from app.modules.projects.service import ProjectService


class MemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session, self._repository = session, MemoryRepository(session)

    async def list(self, actor: AuthenticatedUser) -> list[dict[str, object]]:
        return [self._row(item) for item in await self._repository.list_owned(actor.id)]

    async def create(
        self, actor: AuthenticatedUser, content: str, project_id: UUID | None
    ) -> dict[str, object]:
        if project_id is not None:
            await ProjectService(self._session).require_project_access(project_id, actor)
        now = datetime.now(UTC)
        item = UserMemory(
            id=uuid4(),
            user_id=actor.id,
            project_id=project_id,
            memory_type="PREFERENCE",
            content=content.strip(),
            created_at=now,
            updated_at=now,
        )
        self._session.add(item)
        await self._session.flush()
        return self._row(item)

    async def delete(self, actor: AuthenticatedUser, memory_id: UUID) -> None:
        item = await self._repository.owned(memory_id, actor.id)
        if item is None:
            raise DomainError("RESOURCE_NOT_FOUND", "长期记忆不存在", 404)
        await self._session.delete(item)

    async def recall(self, actor_id: UUID, project_id: UUID | None, query: str) -> list[UserMemory]:
        """仅返回用户自己的全局/当前项目偏好，关键词只是排序过滤，不做跨用户召回。"""
        memories = await self._repository.list_owned(actor_id, project_id)
        words = [word for word in query.lower().split() if len(word) > 1]
        matched = [
            item
            for item in memories
            if not words or any(word in item.content.lower() for word in words)
        ]
        return (matched or memories)[:8]

    @staticmethod
    def _row(item: UserMemory) -> dict[str, object]:
        return {
            "id": str(item.id),
            "project_id": None if item.project_id is None else str(item.project_id),
            "memory_type": item.memory_type,
            "content": item.content,
            "updated_at": item.updated_at,
        }
