"""bid-pipeline 与完整分析任务之间的轻量衔接。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.modules.analysis.full_analysis_service import FullAnalysisService
from app.modules.analysis.models import ProjectAnalysisRun
from app.modules.tender_analysis.downstream_status import update_downstream_stage_for_pipeline
from app.modules.tender_analysis.repository import TenderPipelineRepository


def _pipeline_stage_status(analysis_status: str) -> str:
    if analysis_status == "SUCCEEDED":
        return "SUCCEEDED"
    if analysis_status in {"RUNNING", "REPORT_QUEUED"}:
        return "RUNNING"
    if analysis_status == "FAILED":
        return "FAILED"
    return "PENDING"


async def prepare_pipeline_downstream(
    session: AsyncSession,
    run_id: UUID,
    settings: Settings,
) -> ProjectAnalysisRun | None:
    """为已确认的 bid-pipeline 幂等创建/复用下游完整分析运行。

    这里只做 PostgreSQL 事务内准备，不访问 Redis、不提交事务。调用方提交成功后再
    ``publish_if_queued``。这样 resume job 和周期 reconciler 可以安全复用同一逻辑。
    """
    repository = TenderPipelineRepository(session)
    run = await repository.get_run(run_id, for_update=True)
    if run is None:
        return None
    if run.status != "SUCCEEDED":
        if run.status == "CANCELLED":
            await update_downstream_stage_for_pipeline(
                session,
                run.id,
                status="SKIPPED",
                output={"reason": "review_rejected"},
            )
        return None

    if run.downstream_analysis_run_id is not None:
        existing = await session.get(ProjectAnalysisRun, run.downstream_analysis_run_id)
        if existing is None:
            # 外键正常情况下不应出现；保持 PENDING 让运维/下一轮恢复可见，而不是
            # 悄悄把一个不存在的分析运行标成成功。
            await update_downstream_stage_for_pipeline(
                session,
                run.id,
                status="FAILED",
                output={
                    "reason": "DOWNSTREAM_ANALYSIS_MISSING",
                    "analysis_run_id": str(run.downstream_analysis_run_id),
                },
            )
            return None
        await update_downstream_stage_for_pipeline(
            session,
            run.id,
            status=_pipeline_stage_status(existing.status),
            output={
                "analysis_run_id": str(existing.id),
                "analysis_status": existing.status,
                "reused": True,
            },
        )
        return existing

    try:
        analysis_run = await FullAnalysisService(session, settings).prepare_from_pipeline(
            run.project_id, run.requested_by
        )
    except DomainError as exc:
        if exc.code == "ANALYSIS_ALREADY_RUNNING":
            # 项目上已有另一份不同输入的分析在跑时不能并行创建第二个活动 run；
            # 保持 PENDING 让 reconciler 在旧运行结束后自动补建本轮分析。
            await update_downstream_stage_for_pipeline(
                session,
                run.id,
                status="PENDING",
                output={"reason": exc.code, "message": exc.message},
            )
            return None
        if exc.code in {"ANALYSIS_INPUT_NOT_READY", "PROJECT_ENTERPRISE_REQUIRED"}:
            await update_downstream_stage_for_pipeline(
                session,
                run.id,
                status="SKIPPED",
                output={"reason": exc.code, "message": exc.message},
            )
            return None
        raise

    run.downstream_analysis_run_id = analysis_run.id
    await update_downstream_stage_for_pipeline(
        session,
        run.id,
        status=_pipeline_stage_status(analysis_run.status),
        output={
            "analysis_run_id": str(analysis_run.id),
            "analysis_status": analysis_run.status,
        },
    )
    return analysis_run
