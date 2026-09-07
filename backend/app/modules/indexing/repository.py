"""Evidence 索引任务仓储。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.indexing.models import EvidenceIndexJob


class EvidenceIndexJobRepository:
    """仓储只处理任务记录读写，不承担授权或状态流转决策。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, job: EvidenceIndexJob) -> None:
        self._session.add(job)

    async def get(self, job_id: UUID, *, for_update: bool = False) -> EvidenceIndexJob | None:
        statement = select(EvidenceIndexJob).where(EvidenceIndexJob.id == job_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def get_active(self, project_id: UUID) -> EvidenceIndexJob | None:
        statement = select(EvidenceIndexJob).where(
            EvidenceIndexJob.project_id == project_id,
            EvidenceIndexJob.status.in_(("QUEUED", "RUNNING")),
        )
        return await self._session.scalar(statement)

    async def list_for_recovery(self, stale_before: datetime) -> list[EvidenceIndexJob]:
        statement = (
            select(EvidenceIndexJob)
            .where(
                (EvidenceIndexJob.status == "QUEUED")
                | (
                    (EvidenceIndexJob.status == "RUNNING")
                    & (EvidenceIndexJob.started_at.is_not(None))
                    & (EvidenceIndexJob.started_at < stale_before)
                )
            )
            .order_by(EvidenceIndexJob.created_at, EvidenceIndexJob.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())
