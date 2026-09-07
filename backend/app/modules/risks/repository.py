# ruff: noqa: E501
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.risks.models import ProjectRisk


class RiskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, project_id: UUID, rule_version_id: UUID, subject: str
    ) -> ProjectRisk | None:
        return await self._session.scalar(
            select(ProjectRisk).where(
                ProjectRisk.project_id == project_id,
                ProjectRisk.rule_version_id == rule_version_id,
                ProjectRisk.subject == subject,
            )
        )

    def add(self, risk: ProjectRisk) -> None:
        self._session.add(risk)

    async def delete_except(self, project_id: UUID, keep_ids: list[UUID]) -> None:
        statement = delete(ProjectRisk).where(ProjectRisk.project_id == project_id)
        if keep_ids:
            statement = statement.where(ProjectRisk.id.not_in(keep_ids))
        await self._session.execute(statement)

    async def list(self, project_id: UUID) -> list[ProjectRisk]:
        statement = (
            select(ProjectRisk)
            .where(ProjectRisk.project_id == project_id)
            .order_by(ProjectRisk.severity, ProjectRisk.updated_at.desc(), ProjectRisk.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_by_id(self, risk_id: UUID, *, for_update: bool = False) -> ProjectRisk | None:
        statement = select(ProjectRisk).where(ProjectRisk.id == risk_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)
