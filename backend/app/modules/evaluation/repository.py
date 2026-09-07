"""评测运行的数据访问。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.evaluation.models import EvaluationRun


class EvaluationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_recovery(self, stale_before: datetime) -> list[EvaluationRun]:
        statement = (
            select(EvaluationRun)
            .where(
                (EvaluationRun.status == "QUEUED")
                | ((EvaluationRun.status == "RUNNING") & (EvaluationRun.started_at < stale_before))
            )
            .order_by(EvaluationRun.created_at, EvaluationRun.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())
