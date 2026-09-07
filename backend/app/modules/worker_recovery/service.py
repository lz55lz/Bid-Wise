"""Worker/Redis 中断后的任务恢复。

PostgreSQL 是事实源，队列只负责唤醒。恢复策略保持保守：可幂等的解析/索引/报告任务
自动回到 QUEUED；可能已经写入复杂业务事实的分析任务超时后明确 FAILED。
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.integrations.task_queue import ArqTaskQueue, TaskQueueUnavailable
from app.modules.analysis.full_analysis_service import FullAnalysisService
from app.modules.analysis.repository import FindingAnalysisRepository
from app.modules.analysis.state_machine import transition_finding, transition_full
from app.modules.documents.repository import DocumentRepository
from app.modules.documents.state_machine import transition as transition_document
from app.modules.evaluation.repository import EvaluationRepository
from app.modules.evaluation.state_machine import transition as transition_evaluation
from app.modules.indexing.repository import EvidenceIndexJobRepository
from app.modules.indexing.state_machine import transition as transition_index
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.state_machine import transition as transition_knowledge
from app.modules.reports.repository import ReportRepository
from app.modules.reports.state_machine import transition as transition_report
from app.modules.tender_analysis.downstream_coordinator import prepare_pipeline_downstream
from app.modules.tender_analysis.downstream_status import update_downstream_stage_for_analysis
from app.modules.tender_analysis.repository import TenderPipelineRepository
from app.modules.tender_analysis.state_machine import transition as transition_tender

logger = logging.getLogger(__name__)

# 失联判定必须显著晚于 ARQ 的硬超时（当前 45 分钟）。否则任务刚被 ARQ 取消、
# 清理事务尚未结束时，reconciler 会立刻与其争抢状态。没有 heartbeat 前宁可让真正
# 的崩溃任务多等待一段时间，也不能把仍可能完成的长任务误判为死任务。
_STALE_RUNNING_AFTER = timedelta(minutes=60)


@dataclass(slots=True)
class _RecoveryBatch:
    document_jobs: list[object]
    knowledge_jobs: list[object]
    report_jobs: list[object]
    index_jobs: list[object]
    finding_jobs: list[object]
    full_runs: list[object]
    tender_runs: list[object]
    downstream_pending: list[object]
    evaluation_runs: list[object]

    def has_work(self) -> bool:
        return any(
            (
                self.document_jobs,
                self.knowledge_jobs,
                self.report_jobs,
                self.index_jobs,
                self.finding_jobs,
                self.full_runs,
                self.tender_runs,
                self.downstream_pending,
                self.evaluation_runs,
            )
        )


class WorkerRecoveryService:
    """负责状态修复和补投，不承载具体业务执行逻辑。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._queue = ArqTaskQueue(settings)

    async def reconcile(self) -> None:
        now = datetime.now(UTC)
        batch = await self._load_batch(now - _STALE_RUNNING_AFTER)
        if not batch.has_work():
            return
        await self._normalize_stale_states(batch, now)
        await self._session.commit()
        await self._republish_queued(batch, now)
        await self._recover_downstream(batch.downstream_pending)
        await self._session.commit()

    async def _load_batch(self, stale_before: datetime) -> _RecoveryBatch:
        documents = DocumentRepository(self._session)
        knowledge = KnowledgeRepository(self._session)
        indexes = EvidenceIndexJobRepository(self._session)
        reports = ReportRepository(self._session)
        analysis = FindingAnalysisRepository(self._session)
        tender = TenderPipelineRepository(self._session)
        evaluations = EvaluationRepository(self._session)
        return _RecoveryBatch(
            document_jobs=await documents.list_parse_jobs_for_recovery(stale_before),
            knowledge_jobs=await knowledge.list_parse_jobs_for_recovery(stale_before),
            report_jobs=await reports.list_for_recovery(stale_before),
            index_jobs=await indexes.list_for_recovery(stale_before),
            finding_jobs=await analysis.list_finding_jobs_for_recovery(stale_before),
            full_runs=await analysis.list_full_runs_for_recovery(stale_before),
            tender_runs=await tender.list_runs_for_recovery(stale_before),
            downstream_pending=await tender.list_succeeded_pending_downstream(),
            evaluation_runs=await evaluations.list_for_recovery(stale_before),
        )

    async def _normalize_stale_states(self, batch: _RecoveryBatch, now: datetime) -> None:
        documents = DocumentRepository(self._session)
        knowledge = KnowledgeRepository(self._session)

        for job in batch.document_jobs:
            if job.status == "RUNNING":
                version = await documents.get_version(job.document_version_id)
                if version is not None:
                    transition_document(version, "QUEUED")
            job.status, job.arq_job_id, job.started_at = "QUEUED", None, None

        for job in batch.knowledge_jobs:
            if job.status == "RUNNING":
                document = await knowledge.document(job.knowledge_document_version_id)
                if document is not None:
                    transition_knowledge(document, "QUEUED")
            job.status, job.arq_job_id, job.started_at = "QUEUED", None, None

        for report in batch.report_jobs:
            if report.status == "GENERATING":
                transition_report(report, "QUEUED")
        for job in batch.index_jobs:
            if job.status == "RUNNING":
                transition_index(job, "QUEUED")

        # 复杂分析可能已写业务事实/checkpoint，未证明可重放前 fail-closed。
        for job in batch.finding_jobs:
            if job.status == "RUNNING":
                transition_finding(job, "FAILED")
                job.error_code = "WORKER_INTERRUPTED"
                job.error_message = "Worker 中断，任务未自动重放"
                job.completed_at = now
        for run in batch.full_runs:
            if run.status == "RUNNING":
                transition_full(run, "FAILED")
                run.current_stage = "INTERRUPTED"
                run.error_code = "WORKER_INTERRUPTED"
                run.error_message = "Worker 中断，完整分析未自动重放"
                run.completed_at = now
                await update_downstream_stage_for_analysis(
                    self._session,
                    run.id,
                    status="FAILED",
                    analysis_status=run.status,
                    current_stage=run.current_stage,
                    extra={"error_code": run.error_code},
                )
        for run in batch.tender_runs:
            if run.status == "RUNNING":
                transition_tender(run, "FAILED")
                run.error_code = "WORKER_INTERRUPTED"
                run.error_message = "Worker 中断，投标管线未自动重放"
                run.completed_at = now
        for run in batch.evaluation_runs:
            if run.status == "RUNNING":
                transition_evaluation(run, "FAILED")
                run.error_message = "Worker 中断，评测未自动重放"
                run.completed_at = now

    async def _republish_queued(self, batch: _RecoveryBatch, now: datetime) -> None:
        for job in batch.document_jobs:
            await self._publish(
                lambda job=job: self._queue.publish_document_parse(str(job.id)),
                on_success=lambda value, job=job: setattr(job, "arq_job_id", value),
            )
        for job in batch.knowledge_jobs:
            await self._publish(
                lambda job=job: self._queue.publish_knowledge_parse(str(job.id)),
                on_success=lambda value, job=job: setattr(job, "arq_job_id", value),
            )
        for report in batch.report_jobs:
            await self._publish(
                lambda report=report: self._queue.publish_project_report(str(report.id))
            )
        for job in batch.index_jobs:
            await self._publish(lambda job=job: self._queue.publish_evidence_index(str(job.id)))
        for job in batch.finding_jobs:
            if job.status == "QUEUED":
                await self._publish(
                    lambda job=job: self._queue.publish_finding_analysis(str(job.id))
                )
        for run in batch.full_runs:
            if run.status == "QUEUED":
                await self._publish(lambda run=run: self._queue.publish_full_analysis(str(run.id)))
        for run in batch.tender_runs:
            if run.status == "QUEUED":
                await self._publish(
                    lambda run=run: self._queue.publish_tender_pipeline(str(run.id)),
                    on_success=lambda value, run=run: setattr(run, "arq_job_id", value),
                )
            elif run.status == "RESUME_QUEUED":
                token = _resume_token(run.pending_review)
                if token:
                    await self._publish(
                        lambda run=run, token=token: self._queue.publish_tender_pipeline_resume(
                            str(run.id), token
                        ),
                        on_success=lambda value, run=run: setattr(run, "arq_job_id", value),
                    )
                else:
                    transition_tender(run, "FAILED")
                    run.error_code = "INVALID_REVIEW_STATE"
                    run.error_message = "人工复核恢复令牌缺失"
                    run.completed_at = now
        for run in batch.evaluation_runs:
            if run.status == "QUEUED":
                await self._publish(lambda run=run: self._queue.publish_evaluation(str(run.id)))

    async def _recover_downstream(self, runs: list[object]) -> None:
        for run in runs:
            # rollback 之后 ORM 实例属性会被过期，此时再读 run.id 会触发同步刷新，
            # 在 asyncio 下抛 MissingGreenlet 并污染连接池。这里先取出标量值。
            run_id = run.id
            try:
                analysis_run = await prepare_pipeline_downstream(
                    self._session, run_id, self._settings
                )
                await self._session.commit()
            except Exception:
                await self._session.rollback()
                logger.warning(
                    "恢复投标管线下游分析失败 pipeline_run_id=%s",
                    run_id,
                    exc_info=True,
                )
                continue
            if analysis_run is not None:
                await FullAnalysisService(self._session, self._settings).publish_if_queued(
                    analysis_run
                )

    async def _publish(self, call, *, on_success=None) -> None:
        try:
            result = await call()
        except TaskQueueUnavailable:
            logger.warning("任务队列暂不可用，reconciler 保留 DB 待办等待下次补投")
            return
        if on_success is not None:
            on_success(result)


def _resume_token(pending_review: object) -> str | None:
    if not isinstance(pending_review, dict):
        return None
    value = pending_review.get("resume_token")
    return value if isinstance(value, str) and value else None
