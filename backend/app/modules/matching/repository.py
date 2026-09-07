from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.matching.models import MaterialMatchResult


class MatchingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, project_id: UUID, requirement_id: UUID) -> MaterialMatchResult | None:
        return await self._session.scalar(
            select(MaterialMatchResult).where(
                MaterialMatchResult.project_id == project_id,
                MaterialMatchResult.requirement_id == requirement_id,
            )
        )

    def add(self, item: MaterialMatchResult) -> None:
        self._session.add(item)

    async def list(self, project_id: UUID) -> list[MaterialMatchResult]:
        statement = (
            select(MaterialMatchResult)
            .where(MaterialMatchResult.project_id == project_id)
            .order_by(MaterialMatchResult.updated_at.desc(), MaterialMatchResult.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_by_id(
        self, match_id: UUID, *, for_update: bool = False
    ) -> MaterialMatchResult | None:
        statement = select(MaterialMatchResult).where(MaterialMatchResult.id == match_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)
