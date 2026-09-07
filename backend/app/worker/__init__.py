"""ARQ Worker 入口。

耗时的解析、索引和报告生成只在 Worker 内运行；HTTP 路由只发布任务，
不能直接执行这些长任务。具体任务由各领域模块暴露的 worker_tasks 显式注册。
"""

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.db.session import dispose_database_engine, get_session_factory
from app.integrations.http_client import close_http_clients
from app.integrations.redis_pool import close_redis_pool
from app.modules.analysis.full_analysis_worker import run_full_project_analysis
from app.modules.analysis.worker_tasks import analyze_project_findings
from app.modules.documents.worker_tasks import parse_project_document
from app.modules.evaluation.worker_tasks import run_evaluation
from app.modules.indexing.worker_tasks import index_project_evidences
from app.modules.knowledge.worker_tasks import parse_knowledge_document
from app.modules.reports.worker_tasks import generate_project_report
from app.modules.tender_analysis.worker_tasks import resume_tender_pipeline, run_tender_pipeline
from app.modules.worker_recovery.service import WorkerRecoveryService


async def reconcile_persisted_tasks(_: dict[object, object]) -> None:
    """周期性从 PostgreSQL 补投 QUEUED 任务；ARQ 固定 job_id 保证并发幂等。"""
    async with get_session_factory()() as session:
        await WorkerRecoveryService(session, get_settings()).reconcile()


def _build_redis_settings() -> RedisSettings:
    """从唯一的部署 Redis 地址构造 Worker 连接配置。"""
    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    """供 `arq app.worker.WorkerSettings` 启动的最小 Worker 配置。"""

    # 领域任务在这里显式登记，禁止通过字符串或运行时扫描隐式发现。
    functions = [
        analyze_project_findings,
        run_full_project_analysis,
        generate_project_report,
        index_project_evidences,
        parse_project_document,
        parse_knowledge_document,
        run_evaluation,
        run_tender_pipeline,
        resume_tender_pipeline,
    ]
    redis_settings = _build_redis_settings()
    cron_jobs = [cron(reconcile_persisted_tasks, minute=set(range(60)), unique=True)]

    # ARQ 默认单任务超时只有 300 秒，而 MinerU 解析本身允许最长约 15 分钟轮询。
    # 不显式覆盖时，长文档会先被 Worker 取消，数据库却已经处于 RUNNING。这里把
    # Worker 的硬超时放宽到 45 分钟，同时限制并发，避免多个 100MB 文档解析把
    # 线程、连接和内存同时打满。业务任务自身已经持久化失败状态并提供人工重试，
    # 因此关闭 ARQ 的隐式多次重试，避免非幂等任务被框架自动重放。
    job_timeout = 45 * 60
    max_jobs = 4
    max_tries = 1
    # PostgreSQL 才是任务结果与错误的事实源，不依赖 ARQ result 查询。结果立即清理
    # 可以让同一个稳定 job_id 在明确回到 QUEUED 后立刻重新入队，同时仍保留
    # queue/in-progress 阶段的唯一性。
    keep_result = 0
    # 非幂等业务任务不交给 ARQ 在 CancelledError 后自行猜测是否重放；Worker 中断后
    # 统一由数据库状态恢复策略决定“重新排队”还是“标失败等待人工重启”。
    retry_jobs = False
    # 收到 SIGTERM 后给正在执行的任务一小段收尾时间，Compose/K8s 滚动发布时
    # 尽量让数据库状态落到确定终态；超时后仍由恢复服务处理残留任务。
    job_completion_wait = 30
    health_check_interval = 30

    @staticmethod
    async def on_shutdown(_: object) -> None:
        await close_redis_pool()
        await close_http_clients()
        await dispose_database_engine()
