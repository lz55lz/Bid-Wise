"""招标分析持久化访问；不在仓储层决定节点跳转或人工复核规则。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tender_analysis.models import TenderPipelineRun, TenderPipelineStage


class TenderPipelineRepository:
    """管线运行与阶段记录的最小数据访问边界。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add_run(self, run: TenderPipelineRun) -> None:
        self._session.add(run)

    def add_stages(self, stages: list[TenderPipelineStage]) -> None:
        self._session.add_all(stages)


    async def get_active_for_document_version(
        self, document_version_id: UUID
    ) -> TenderPipelineRun | None:
        return await self._session.scalar(
            select(TenderPipelineRun).where(
                TenderPipelineRun.document_version_id == document_version_id,
                TenderPipelineRun.status.in_(
                    ("QUEUED", "RUNNING", "WAITING_HUMAN_REVIEW", "RESUME_QUEUED")
                ),
            )
        )

    async def get_run(self, run_id: UUID, *, for_update: bool = False) -> TenderPipelineRun | None:
        statement = select(TenderPipelineRun).where(TenderPipelineRun.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_runs(self, project_id: UUID, limit: int) -> list[TenderPipelineRun]:
        """按最近提交时间返回项目运行历史，供前端恢复轮询或人工审核入口。"""
        statement = (
            select(TenderPipelineRun)
            .where(TenderPipelineRun.project_id == project_id)
            .order_by(TenderPipelineRun.created_at.desc(), TenderPipelineRun.id)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_stages_for_runs(
        self, pipeline_run_ids: list[UUID]
    ) -> list[TenderPipelineStage]:
        if not pipeline_run_ids:
            return []
        statement = select(TenderPipelineStage).where(
            TenderPipelineStage.pipeline_run_id.in_(pipeline_run_ids)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_stage(
        self, pipeline_run_id: UUID, stage_name: str, *, for_update: bool = False
    ) -> TenderPipelineStage | None:
        statement = select(TenderPipelineStage).where(
            TenderPipelineStage.pipeline_run_id == pipeline_run_id,
            TenderPipelineStage.stage_name == stage_name,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_runs_for_recovery(self, stale_before: datetime) -> list[TenderPipelineRun]:
        statement = (
            select(TenderPipelineRun)
            .where(
                (TenderPipelineRun.status.in_(("QUEUED", "RESUME_QUEUED")))
                | (
                    (TenderPipelineRun.status == "RUNNING")
                    & (TenderPipelineRun.started_at < stale_before)
                )
            )
            .order_by(TenderPipelineRun.created_at, TenderPipelineRun.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_succeeded_pending_downstream(self, limit: int = 50) -> list[TenderPipelineRun]:
        """查找人工事实已落库但下游完整分析尚未衔接的运行。

        只扫描 downstream stage 仍为 PENDING 的成功运行；SKIPPED/FAILED 不会每分钟
        反复尝试。这个查询专门覆盖 resume job 在“提交人工事实”后异常退出的窗口。
        """
        statement = (
            select(TenderPipelineRun)
            .join(
                TenderPipelineStage,
                TenderPipelineStage.pipeline_run_id == TenderPipelineRun.id,
            )
            .where(
                TenderPipelineRun.status == "SUCCEEDED",
                TenderPipelineRun.downstream_analysis_run_id.is_(None),
                TenderPipelineStage.stage_name == "downstream_analysis",
                TenderPipelineStage.status == "PENDING",
            )
            .order_by(TenderPipelineRun.completed_at, TenderPipelineRun.id)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())
