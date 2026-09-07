"""投标分析 ARQ 任务。

ARQ 只负责唤醒 Worker；运行状态、人工审核事实和 LangGraph thread_id 均以
PostgreSQL 为准。run/resume 共用同一图装配与执行路径，避免两条分支长期漂移。
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.integrations.langgraph_checkpoint import open_checkpoint_saver
from app.modules.analysis.full_analysis_service import FullAnalysisService
from app.modules.tender_analysis.downstream_coordinator import prepare_pipeline_downstream
from app.modules.tender_analysis.graph_factory import build_tender_pipeline
from app.modules.tender_analysis.models import TenderPipelineRun, TenderPipelineStage, TenderTag
from app.modules.tender_analysis.repository import TenderPipelineRepository
from app.modules.tender_analysis.state_machine import transition

logger = logging.getLogger(__name__)


async def run_tender_pipeline(_: dict[object, object], run_id: str) -> dict[str, str]:
    """执行新的投标分析；同一 run 只能从 QUEUED 原子进入 RUNNING。"""
    settings = get_settings()
    async with get_session_factory()() as session:
        repository = TenderPipelineRepository(session)
        run = await repository.get_run(UUID(run_id), for_update=True)
        if run is None or run.status != "QUEUED":
            return {"run_id": run_id, "status": "ignored"}
        transition(run, "RUNNING")
        run.attempt += 1
        run.started_at = datetime.now(UTC)
        run.error_code = None
        run.error_message = None
        await session.commit()

        initial_state = {
            "pipeline_run_id": str(run.id),
            "document_version_id": str(run.document_version_id),
        }
        try:
            result = await _invoke_pipeline(session, settings, run, initial_state)
            if "__interrupt__" in result:
                await _persist_interrupt(session, run, result)
                return {"run_id": run_id, "status": "waiting_human_review"}
            await session.commit()
            if run.status == "SUCCEEDED":
                await _dispatch_downstream(session, settings, run)
            return {"run_id": run_id, "status": run.status.lower()}
        except Exception as exc:
            logger.exception("招标字段提取失败 pipeline_run_id=%s", run.id)
            await _mark_failed(
                session,
                repository,
                run.id,
                "TENDER_PIPELINE_FAILED",
                _pipeline_failure_message(exc),
            )
            raise


async def resume_tender_pipeline(_: dict[object, object], run_id: str) -> dict[str, str]:
    """恢复人工复核后的同一 LangGraph thread；队列消息只携带持久化 run_id。"""
    settings = get_settings()
    async with get_session_factory()() as session:
        repository = TenderPipelineRepository(session)
        run = await repository.get_run(UUID(run_id), for_update=True)
        if run is None or run.status != "RESUME_QUEUED":
            return {"run_id": run_id, "status": "ignored"}
        decision = _resume_decision(run)
        if decision is None:
            transition(run, "FAILED")
            run.error_code = "INVALID_REVIEW_STATE"
            run.error_message = "人工复核恢复数据缺失"
            run.completed_at = datetime.now(UTC)
            await session.commit()
            return {"run_id": run_id, "status": "failed"}

        transition(run, "RUNNING")
        run.error_code = None
        run.error_message = None
        await session.commit()
        try:
            result = await _invoke_pipeline(session, settings, run, Command(resume=decision))
            if "__interrupt__" in result:
                await _persist_interrupt(session, run, result)
                return {"run_id": run_id, "status": "waiting_human_review"}

            # 人工确认事实先独立提交。下游完整分析是另一项长任务；即使 Redis/Worker
            # 此刻失败，也由 recovery 对 SUCCEEDED + PENDING 阶段补建，不回滚人审事实。
            await session.commit()
            await _dispatch_downstream(session, settings, run)
            return {"run_id": run_id, "status": run.status.lower()}
        except Exception:
            await _mark_failed(
                session,
                repository,
                run.id,
                "TENDER_PIPELINE_RESUME_FAILED",
                "人工复核恢复失败",
            )
            raise


async def _invoke_pipeline(
    session: AsyncSession,
    settings: Settings,
    run: TenderPipelineRun,
    input_state: object,
) -> dict[str, object]:
    """共用图装配、checkpoint 与 thread 配置，防止 run/resume 两套逻辑漂移。"""
    graph = build_tender_pipeline(session, settings)
    config = {"configurable": {"thread_id": run.thread_id}}
    async with open_checkpoint_saver(settings) as saver:
        compiled = graph.compile(checkpointer=saver)
        result = await compiled.ainvoke(input_state, config=config)
    return result


async def _persist_interrupt(
    session: AsyncSession,
    run: TenderPipelineRun,
    result: dict[str, object],
) -> None:
    transition(run, "WAITING_HUMAN_REVIEW")
    run.pending_review = await _build_pending_review(session, result)
    await _mark_human_review_stage(session, run.id, "WAITING_HUMAN_REVIEW")
    await session.commit()


async def _dispatch_downstream(
    session: AsyncSession,
    settings: Settings,
    run: TenderPipelineRun,
) -> None:
    # rollback 之后 ORM 属性会过期，读取 run.id 会触发同步刷新并在 asyncio 下抛
    # MissingGreenlet；先固化标量值，保证异常分支的告警本身不再抛出新异常。
    run_id = run.id
    try:
        analysis_run = await prepare_pipeline_downstream(session, run_id, settings)
        await session.commit()
        if analysis_run is not None:
            await FullAnalysisService(session, settings).publish_if_queued(analysis_run)
    except Exception:
        # 已确认事实不能被下游瞬时故障回滚；downstream_analysis 保持 PENDING，
        # WorkerRecoveryService 会在后续周期补建/补投，但必须留下可定位的告警。
        await session.rollback()
        logger.warning(
            "投标管线下游分析派发失败 pipeline_run_id=%s", run_id, exc_info=True
        )


async def _mark_failed(
    session: AsyncSession,
    repository: TenderPipelineRepository,
    run_id: UUID,
    code: str,
    message: str,
) -> None:
    await session.rollback()
    run = await repository.get_run(run_id, for_update=True)
    if run is None or run.status not in {"RUNNING", "RESUME_QUEUED"}:
        return
    if run.status == "RESUME_QUEUED":
        transition(run, "RUNNING")
    transition(run, "FAILED")
    run.error_code = code
    run.error_message = message
    run.completed_at = datetime.now(UTC)
    await session.commit()


def _resume_decision(run: TenderPipelineRun) -> dict[str, object] | None:
    pending = run.pending_review or {}
    decision = pending.get("resume_decision") if isinstance(pending, dict) else None
    if not isinstance(decision, dict) or decision.get("decision") not in {"approved", "rejected"}:
        return None
    return decision


def _pipeline_failure_message(exc: Exception) -> str:
    """将常见可恢复故障转成产品可理解的提示，不向界面泄露模型响应全文。"""
    detail = str(exc)
    if "模型未返回合法标签 JSON 对象" in detail or "items 数组" in detail:
        return "模型返回的字段结果格式异常，已停止本轮提取；请重新提取。"
    if "LLM 服务未配置" in detail:
        return "字段提取模型未配置，暂时无法生成需求。"
    if "timeout" in detail.lower():
        return "字段提取模型响应超时，请稍后重新提取。"
    return f"字段提取发生内部错误（{type(exc).__name__}），请重新提取。"


async def _build_pending_review(
    session: AsyncSession, result: dict[str, object]
) -> dict[str, object]:
    """生成审核页受控元数据；字段定义只来自服务端标签基线。"""
    extracted = result.get("extracted_tags", {})
    issues = result.get("validation_issues", [])
    validation_issues = issues if isinstance(issues, list) else []
    # 审批时允许在受控字段范围内补充，而不只限于发生校验异常的字段。
    # 标签定义仍然完全来自服务端目录，前端不能自造字段或约束。
    rows = await session.scalars(
        select(TenderTag).where(TenderTag.is_active.is_(True)).order_by(TenderTag.code)
    )
    tags = [
        {
            "code": tag.code,
            "name": tag.name,
            "level_code": tag.level_code,
            "data_type": tag.data_type,
            "is_required": tag.is_required,
            "is_multi_value": tag.is_multi_value,
            "value_example": tag.value_example,
        }
        for tag in rows.all()
    ]
    return {
        "validation_issues": validation_issues,
        "extracted_tags": extracted if isinstance(extracted, dict) else {},
        "extraction_failures": result.get("extraction_failures", []),
        "review_tag_codes": result.get("review_tag_codes", []),
        "auto_approved_tag_codes": result.get("auto_approved_tag_codes", []),
        "tag_catalog": tags,
    }


async def _mark_human_review_stage(session: AsyncSession, run_id: UUID, status: str) -> None:
    stage = await session.scalar(
        select(TenderPipelineStage)
        .where(
            TenderPipelineStage.pipeline_run_id == run_id,
            TenderPipelineStage.stage_name == "human_review",
        )
        .with_for_update()
    )
    if stage is not None:
        stage.status = status
