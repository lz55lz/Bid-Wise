"""同步 bid-pipeline 与下游完整分析的业务可见状态。"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tender_analysis.models import TenderPipelineRun, TenderPipelineStage


async def update_downstream_stage_for_analysis(
    session: AsyncSession,
    analysis_run_id: UUID,
    *,
    status: str,
    analysis_status: str,
    current_stage: str | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    """按 ``ProjectAnalysisRun`` 反向更新来源 bid-pipeline 的下游阶段。

    PipelineRun 本身在人工确认事实落库后即可成功；Matching/Risk/Report 是另一条
    ARQ 长任务，因此通过独立 stage 暴露其进度，避免把两个生命周期混成一个状态。
    """
    rows = await session.execute(
        select(TenderPipelineRun, TenderPipelineStage)
        .join(
            TenderPipelineStage,
            TenderPipelineStage.pipeline_run_id == TenderPipelineRun.id,
        )
        .where(
            TenderPipelineRun.downstream_analysis_run_id == analysis_run_id,
            TenderPipelineStage.stage_name == "downstream_analysis",
        )
        .with_for_update()
    )
    pairs = rows.all()
    if not pairs:
        return
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "analysis_run_id": str(analysis_run_id),
        "analysis_status": analysis_status,
    }
    if current_stage:
        payload["current_stage"] = current_stage
    if extra:
        payload.update(extra)
    # 同一项目的多个文档管线可能复用同一个活动分析运行；所有来源管线都必须看到
    # 同一份下游状态，而不能只更新 SQL 查询碰巧返回的第一条。
    for _run, stage in pairs:
        stage.status = status
        stage.output_summary = dict(payload)
        if status == "RUNNING" and stage.started_at is None:
            stage.started_at = now
        if status in {"SUCCEEDED", "FAILED", "SKIPPED"}:
            stage.completed_at = now
        else:
            stage.completed_at = None


async def update_downstream_stage_for_pipeline(
    session: AsyncSession,
    pipeline_run_id: UUID,
    *,
    status: str,
    output: dict[str, object],
) -> None:
    """按 PipelineRun ID 更新下游阶段，供 resume/reconciler 准备阶段使用。"""
    stage = await session.scalar(
        select(TenderPipelineStage)
        .where(
            TenderPipelineStage.pipeline_run_id == pipeline_run_id,
            TenderPipelineStage.stage_name == "downstream_analysis",
        )
        .with_for_update()
    )
    if stage is None:
        return
    now = datetime.now(UTC)
    stage.status = status
    stage.output_summary = output
    if status == "RUNNING" and stage.started_at is None:
        stage.started_at = now
    if status in {"SUCCEEDED", "FAILED", "SKIPPED"}:
        stage.completed_at = now
    else:
        stage.completed_at = None
