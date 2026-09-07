"""项目报告仓储。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reports.models import ProjectReport


class ReportRepository:
    """不在仓储层处理报告输入、模型或授权。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, report_id: UUID, *, for_update: bool = False) -> ProjectReport | None:
        statement = select(ProjectReport).where(ProjectReport.id == report_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return await self._session.scalar(statement)

    async def get_for_project(
        self, project_id: UUID, *, for_update: bool = False
    ) -> ProjectReport | None:
        statement = select(ProjectReport).where(ProjectReport.project_id == project_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return await self._session.scalar(statement)

    async def list_for_recovery(self, stale_before: datetime) -> list[ProjectReport]:
        statement = (
            select(ProjectReport)
            .where(
                (ProjectReport.status == "QUEUED")
                | (
                    (ProjectReport.status == "GENERATING")
                    & (ProjectReport.started_at.is_not(None))
                    & (ProjectReport.started_at < stale_before)
                )
            )
            .order_by(ProjectReport.created_at, ProjectReport.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_latest_ready(self, project_id: UUID) -> ProjectReport | None:
        report = await self.get_for_project(project_id)
        return report if report is not None and report.status == "READY" else None

    async def get_latest_retrievable(self, project_id: UUID) -> ProjectReport | None:
        report = await self.get_for_project(project_id)
        return (
            report
            if report is not None and report.status == "READY" and not report.is_stale
            else None
        )

    async def mark_stale_for_projects(self, project_ids: set[UUID]) -> int:
        if not project_ids:
            return 0
        reports = list(
            (
                await self._session.scalars(
                    select(ProjectReport).where(
                        ProjectReport.project_id.in_(project_ids),
                        ProjectReport.status.in_(("QUEUED", "GENERATING", "READY")),
                        ProjectReport.is_stale.is_(False),
                    )
                )
            ).all()
        )
        for report in reports:
            report.is_stale = True
        return len(reports)

    def add(self, report: ProjectReport) -> None:
        self._session.add(report)
