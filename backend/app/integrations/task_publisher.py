"""任务发布器 — 统一通过 ARQ (Redis) 入队异步任务。

替代已删除的 CeleryTaskPublisher；Task.celery_task_id 列沿用存 ARQ job_id。
"""
import asyncio
import concurrent.futures
from typing import Any, Protocol
from uuid import UUID


def enqueue_arq(function: str, *args: Any) -> str:
    """同步入队 ARQ 任务并返回 job_id。

    在事件循环内调用时转到独立线程执行，避免 asyncio.run 抛 RuntimeError。
    """
    from arq import create_pool
    from arq.connections import RedisSettings

    from app.core.config import get_settings

    settings = get_settings()
    if not settings.redis_url:
        return ""

    async def _enqueue() -> str:
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            job = await pool.enqueue_job(function, *args)
            return job.job_id if job else ""
        finally:
            await pool.close()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_enqueue())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(_enqueue())).result()


class TaskPublisher(Protocol):
    def publish_run_match(self, task_id: UUID, project_id: UUID) -> str: ...

    def publish_run_risk_check(self, task_id: UUID, project_id: UUID) -> str: ...

    def publish_generate_decision(self, task_id: UUID, project_id: UUID) -> str: ...

    def publish_generate_report(self, task_id: UUID, report_id: UUID) -> str: ...

    def publish_run_project_analysis(self, task_id: UUID, analysis_run_id: UUID) -> str: ...

    def publish_integration_run(
        self, integration_run_id: UUID, project_id: UUID, payload: dict[str, object]
    ) -> str: ...


class ArqTaskPublisher:
    """把异步任务发布到 ARQ 队列（参数对齐 app/worker.py 的任务函数）。"""

    def publish_run_match(self, task_id: UUID, project_id: UUID) -> str:
        return enqueue_arq("run_match", str(task_id), str(project_id))

    def publish_run_risk_check(self, task_id: UUID, project_id: UUID) -> str:
        return enqueue_arq("run_risk_check", str(task_id), str(project_id))

    def publish_generate_decision(self, task_id: UUID, project_id: UUID) -> str:
        return enqueue_arq("generate_decision", str(task_id), str(project_id))

    def publish_generate_report(self, task_id: UUID, report_id: UUID) -> str:
        return enqueue_arq("generate_report", str(task_id), str(report_id))

    def publish_run_project_analysis(self, task_id: UUID, analysis_run_id: UUID) -> str:
        return enqueue_arq("run_project_analysis", str(task_id), str(analysis_run_id))

    def publish_integration_run(
        self, integration_run_id: UUID, project_id: UUID, payload: dict[str, object]
    ) -> str:
        return enqueue_arq("run_integration", str(integration_run_id), str(project_id), payload)
