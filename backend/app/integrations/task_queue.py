"""ARQ 队列发布适配器。

PostgreSQL 中的任务/运行记录是事实源；Redis 仅负责唤醒 Worker。所有普通任务均由
持久化 ID 派生稳定 job_id，因此 API 首次发布和周期 reconciler 补投可以安全重入。
"""

from app.core.config import Settings
from app.integrations.redis_pool import borrow_redis_pool, invalidate_redis_pool


class TaskQueueUnavailable(Exception):
    """Redis 队列未配置或不可用。"""


class ArqTaskQueue:
    """只发布持久化任务 ID，业务正文和权限范围始终由 Worker 从 PostgreSQL 回查。"""

    def __init__(self, settings: Settings) -> None:
        self._redis_url = settings.redis_url

    async def _enqueue(self, function: str, record_id: str, *, job_id: str) -> str:
        try:
            async with borrow_redis_pool(self._redis_url) as pool:
                job = await pool.enqueue_job(function, record_id, _job_id=job_id)
            # 固定 job_id 已经在队列/执行中时 ARQ 返回 None，这是幂等命中而非失败。
            return job.job_id if job is not None else job_id
        except Exception as exc:
            # 连接已失效时下次发布重新建池；DB 中 QUEUED 仍由 reconciler 兜底。
            await invalidate_redis_pool()
            raise TaskQueueUnavailable("任务队列暂不可用") from exc

    async def _publish(self, function: str, record_id: str, *, prefix: str) -> str:
        return await self._enqueue(function, record_id, job_id=f"{prefix}:{record_id}")

    async def publish_document_parse(self, job_id: str) -> str:
        return await self._publish("parse_project_document", job_id, prefix="document-parse")

    async def publish_knowledge_parse(self, job_id: str) -> str:
        return await self._publish("parse_knowledge_document", job_id, prefix="knowledge-parse")

    async def publish_evaluation(self, run_id: str) -> str:
        return await self._publish("run_evaluation", run_id, prefix="evaluation")

    async def publish_finding_analysis(self, job_id: str) -> str:
        return await self._publish("analyze_project_findings", job_id, prefix="finding-analysis")

    async def publish_evidence_index(self, job_id: str) -> str:
        return await self._publish("index_project_evidences", job_id, prefix="evidence-index")

    async def publish_project_report(self, report_id: str) -> str:
        return await self._publish("generate_project_report", report_id, prefix="project-report")

    async def publish_full_analysis(self, run_id: str) -> str:
        return await self._publish("run_full_project_analysis", run_id, prefix="full-analysis")

    async def publish_tender_pipeline(self, run_id: str) -> str:
        return await self._publish("run_tender_pipeline", run_id, prefix="tender-pipeline")

    async def publish_tender_pipeline_resume(self, run_id: str, resume_token: str) -> str:
        """人工审核恢复仍只传 run_id；token 仅参与唯一 job_id，便于同一 run 多次审核。"""
        return await self._enqueue(
            "resume_tender_pipeline",
            run_id,
            job_id=f"tender-pipeline-resume:{run_id}:{resume_token}",
        )
