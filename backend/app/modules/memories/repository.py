from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.memories.models import UserMemory


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_owned(self, user_id: UUID, project_id: UUID | None = None) -> list[UserMemory]:
        statement = select(UserMemory).where(UserMemory.user_id == user_id)
        if project_id is not None:
            statement = statement.where(UserMemory.project_id.in_((None, project_id)))
        return list(
            (await self._session.scalars(statement.order_by(UserMemory.updated_at.desc()))).all()
        )

    async def owned(self, memory_id: UUID, user_id: UUID) -> UserMemory | None:
        return await self._session.scalar(
            select(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user_id)
        )
