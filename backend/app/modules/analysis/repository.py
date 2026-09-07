"""发现项分析任务仓储。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analysis.models import FindingAnalysisJob, ProjectAnalysisRun


class FindingAnalysisRepository:
    """仓储只做任务数据读取，互斥和输入判断由服务层负责。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, job_id: UUID, *, for_update: bool = False) -> FindingAnalysisJob | None:
        statement = select(FindingAnalysisJob).where(FindingAnalysisJob.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def get_reusable(self, project_id: UUID, input_hash: str) -> FindingAnalysisJob | None:
        """相同输入复用最近成功任务，避免重复消耗模型和生成重复候选。"""
        statement = (
            select(FindingAnalysisJob)
            .where(
                FindingAnalysisJob.project_id == project_id,
                FindingAnalysisJob.input_hash == input_hash,
                FindingAnalysisJob.status == "SUCCEEDED",
            )
            .order_by(FindingAnalysisJob.completed_at.desc())
        )
        return await self._session.scalar(statement)

    async def get_active(self, project_id: UUID) -> FindingAnalysisJob | None:
        statement = select(FindingAnalysisJob).where(
            FindingAnalysisJob.project_id == project_id,
            FindingAnalysisJob.status.in_(("QUEUED", "RUNNING")),
        )
        return await self._session.scalar(statement)

    def add(self, job: FindingAnalysisJob) -> None:
        self._session.add(job)

    async def list_finding_jobs_for_recovery(
        self, stale_before: datetime
    ) -> list[FindingAnalysisJob]:
        statement = (
            select(FindingAnalysisJob)
            .where(
                (FindingAnalysisJob.status == "QUEUED")
                | (
                    (FindingAnalysisJob.status == "RUNNING")
                    & (FindingAnalysisJob.started_at < stale_before)
                )
            )
            .order_by(FindingAnalysisJob.created_at, FindingAnalysisJob.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_full_runs_for_recovery(self, stale_before: datetime) -> list[ProjectAnalysisRun]:
        statement = (
            select(ProjectAnalysisRun)
            .where(
                (ProjectAnalysisRun.status == "QUEUED")
                | (
                    (ProjectAnalysisRun.status == "RUNNING")
                    & (ProjectAnalysisRun.started_at < stale_before)
                )
            )
            .order_by(ProjectAnalysisRun.created_at, ProjectAnalysisRun.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())
