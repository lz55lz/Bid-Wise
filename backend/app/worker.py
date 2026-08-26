"""ARQ Worker — 执行 bid pipeline + knowledge_pipeline 任务

启动方式（Windows + uv 环境）：
    cd backend
    python start_worker.py

ARQ 从 Redis 队列取任务，分流执行：
  - run_bid_pipeline：TENDER 文档，LangGraph 全流程
  - run_knowledge_pipeline：LEGAL/CASE 文档，0 次 LLM 调用
"""

import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from arq.connections import RedisSettings
from arq.worker import Worker

from app.core.config import get_settings
from app.services.knowledge_pipeline import (
    ingest_knowledge_document,  # noqa: F401  提到顶部供 patch
)

logger = logging.getLogger(__name__)


async def configure_worker_logging(_ctx: dict[str, Any]) -> None:
    """Send ARQ diagnostics to the same rotating file as the API process."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Do not persist provider request URLs: they can include access tokens.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log_path = Path(__file__).resolve().parents[2] / "logs" / "app.log"
    log_path.parent.mkdir(exist_ok=True)
    if not any(getattr(handler, "baseFilename", None) == str(log_path) for handler in root.handlers):
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    logger.info("[Worker] unified file logging is ready")


async def run_bid_pipeline(
    ctx: dict[str, Any],
    document_version_id: str,
    project_id: str,
    enterprise_name: str,
    thread_id: str,
) -> dict[str, Any]:
    """ARQ 任务函数：执行完整 bid pipeline（TENDER / ENTERPRISE 文档）。

    Args:
        ctx: ARQ 上下文（包含 redis 连接等）
        document_version_id: app.document_versions.id（UUID 字符串）
        project_id: 项目 UUID 字符串；ENTERPRISE 文档传空串
        enterprise_name: 企业名称（ENTERPRISE 材料提取用）
        thread_id: LangGraph 管线线程 ID（document_versions.pipeline_thread_id）
    """
    from uuid import UUID

    from app.services.bid_pipeline.graph import get_compiled_graph
    from app.services.bid_pipeline.state import BidState

    version_uuid = UUID(document_version_id)
    logger.info(f"[{thread_id}] ARQ task started, version={version_uuid}")

    try:
        parsed_project_id = None
        try:
            parsed_project_id = UUID(project_id) if project_id else None
        except (ValueError, AttributeError):
            logger.warning(f"[{thread_id}] invalid project_id={project_id}, continuing without it")

        state = BidState(
            doc_id=0,  # 新链路以 version_id 为准，doc_id 仅兼容旧 bid_document 数据
            version_id=version_uuid,
            project_id=parsed_project_id,
            doc_name="",
            parse_status="pending",
            raw_text="",
            enterprise_name=enterprise_name,
            thread_id=thread_id,
            current_stage="parse",
        )

        compiled = get_compiled_graph(async_checkpoint=False)
        config = {"configurable": {"thread_id": thread_id}}

        last_event: dict[str, Any] = {}
        stage_count = 0
        async for event in compiled.astream(state, config=config, stream_mode="values"):
            stage = event.get("current_stage", "unknown")
            logger.info(f"[{thread_id}] stage: {stage}")
            last_event = event
            stage_count += 1
            if stage_count > 50:
                logger.warning(f"[{thread_id}] too many stages ({stage_count}), breaking")
                break

        # parse 失败时图在 parse 后直达 END，必须据此判定失败而不是当作完成
        if last_event.get("parse_status") == "error":
            error_message = last_event.get("parse_error", "文档解析失败，所有解析器均未产出内容")
            _update_version_status(
                version_uuid,
                "FAILED",
                "PARSE_FAILED",
                error_message,
            )
            _update_pipeline_task_status(version_uuid, "FAILED", "PARSE_FAILED", error_message)
            logger.error(f"[{thread_id}] parse failed, version={version_uuid}")
            return {
                "status": "failed",
                "document_version_id": document_version_id,
                "thread_id": thread_id,
                "error": "parse failed",
            }

        # 旧 bid_* 标签链路用于即时报告；统一分析以 Requirement 为事实输入。
        # 在同一份已清洗、已证据化的节点上补齐 Requirement 候选，后续必须经过人工确认。
        from app.db.session import get_session_factory
        from app.integrations.ai.embedding import BgeM3Client
        from app.integrations.ai.llm import DeepSeekV4FlashClient
        from app.integrations.vector_store import PgVectorStore
        from app.services.clause_candidate_recall_service import ClauseCandidateRecallService
        from app.services.requirement_extraction_service import RequirementExtractionService
        from app.services.tender_clause_service import TenderClauseService

        requirement_session = get_session_factory()()
        try:
            clause_count = TenderClauseService(requirement_session).rebuild(version_uuid)
            recall_summary = ClauseCandidateRecallService(
                requirement_session,
                embedding_client=BgeM3Client(get_settings()),
                vector_store=PgVectorStore(get_settings()),
            ).select_for_extraction(version_uuid)
            requirement_count = RequirementExtractionService(
                requirement_session, DeepSeekV4FlashClient(get_settings())
            ).do_extract(version_uuid)
        finally:
            requirement_session.close()
        logger.info(
            "[%s] built %d clauses; recall eligible=%d rule=%d llm=%d hybrid=%s; "
            "extracted %d requirement candidates for human review",
            thread_id,
            clause_count,
            recall_summary.eligible_clauses,
            recall_summary.rule_direct_clauses,
            recall_summary.llm_selected_clauses,
            recall_summary.hybrid_status,
            requirement_count,
        )

        logger.info(f"[{thread_id}] ARQ task completed, version={version_uuid}")

        # 更新 DocumentVersion.parse_status → READY
        _update_version_status(version_uuid, "READY")
        _update_pipeline_task_status(version_uuid, "SUCCEEDED")

        return {
            "status": "completed",
            "document_version_id": document_version_id,
            "thread_id": thread_id,
        }

    except Exception as e:
        logger.error(f"[{thread_id}] ARQ task failed: {e}", exc_info=True)

        # 更新 DocumentVersion.parse_status → FAILED
        _update_version_status(version_uuid, "FAILED", "PIPELINE_FAILED", str(e))
        _update_pipeline_task_status(version_uuid, "FAILED", "PIPELINE_FAILED", str(e))

        return {
            "status": "failed",
            "document_version_id": document_version_id,
            "thread_id": thread_id,
            "error": str(e),
        }


async def process_im_message(
    ctx: dict[str, Any], channel_id: str, incoming_payload: dict[str, Any]
) -> dict[str, Any]:
    """Run a verified IM event outside the HTTP callback lifecycle."""
    del ctx
    from app.db.models.im_channel import IMChannel
    from app.db.session import get_session_factory
    from app.integrations.im.runtime import build_adapter, serialize_channel
    from app.integrations.im.schemas import IncomingMessage
    from app.integrations.im.service import IMService

    db = get_session_factory()()
    try:
        channel = db.get(IMChannel, channel_id)
        if channel is None or channel.deleted_at is not None or not channel.enabled:
            logger.warning("[IM] discarded callback for unavailable channel=%s", channel_id)
            return {"status": "discarded", "channel_id": channel_id}
        incoming = IncomingMessage.model_validate(incoming_payload)
        await IMService().handle_message(
            channel_id,
            incoming,
            build_adapter(serialize_channel(channel)),
            channel_data=serialize_channel(channel),
            db_session=db,
        )
        return {"status": "completed", "channel_id": channel_id, "message_id": incoming.message_id}
    finally:
        db.close()


async def run_knowledge_pipeline(
    ctx: dict[str, Any],
    document_version_id: str,
    document_id: str,
    knowledge_version_id: str,
    chunk_type: str,
    title: str,
    authority: str | None,
    source_reference: str,
    content_summary: str,
    actor_id: str,
    object_key: str,
    file_name: str,
    mime_type: str,
) -> dict[str, Any]:
    """ARQ 任务函数：执行 knowledge_pipeline（LEGAL/CASE 入库）。

    流程：document_ingest.downloader → knowledge_pipeline.ingest_knowledge_document
    0 次 LLM 调用；失败抛异常由 ARQ 重试。

    ingest_knowledge_document 在模块顶部 import（供 patch 用），
    cleanup_temp_file / download_document 仍是函数内 import（与原代码一致）。
    """
    from uuid import UUID

    from app.services.document_ingest import cleanup_temp_file, download_document

    logger.info(
        "[knowledge_pipeline] ARQ task started: version=%s chunk_type=%s",
        document_version_id,
        chunk_type,
    )

    settings = get_settings()
    file_path: str | None = None

    try:
        # 1. 下载文件
        file_path = download_document(
            doc_url=f"minio://{settings.minio_bucket}/{object_key}",
            file_name=file_name,
            settings=settings,
        )
        if not file_path:
            raise RuntimeError(f"failed to download object_key={object_key}")

        # 2. 整条链路
        result = ingest_knowledge_document(
            file_path=file_path,
            mime_type=mime_type or "application/pdf",
            document_version_id=UUID(document_version_id),
            knowledge_version_id=UUID(knowledge_version_id),
            chunk_type=chunk_type,
            actor_id=UUID(actor_id),
        )

        # 3. 更新 DocumentVersion.parse_status → READY
        _update_knowledge_version_status(UUID(document_version_id), "READY")
        _update_pipeline_task_status(UUID(document_version_id), "SUCCEEDED")

        logger.info(f"[knowledge_pipeline] ARQ task completed: chunks={result.chunk_count}")
        return {
            "status": "completed",
            "document_version_id": document_version_id,
            "knowledge_entry_id": str(result.knowledge_entry_id),
            "knowledge_version_id": str(result.knowledge_version_id),
            "chunk_count": result.chunk_count,
        }

    except Exception as e:
        logger.error(f"[knowledge_pipeline] ARQ task failed: {e}", exc_info=True)
        _update_knowledge_version_status(
            UUID(document_version_id), "FAILED", "KNOWLEDGE_PIPELINE_FAILED", str(e)
        )
        _update_pipeline_task_status(
            UUID(document_version_id), "FAILED", "KNOWLEDGE_PIPELINE_FAILED", str(e)
        )
        # 抛出异常让 ARQ 按 max_tries 重试；重试成功会覆盖回 READY
        raise
    finally:
        cleanup_temp_file(file_path)


def _update_knowledge_version_status(
    version_id: Any,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """knowledge_pipeline 完成后更新 DocumentVersion.parse_status。"""
    from app.db.models import DocumentVersion
    from app.db.session import get_session_factory

    session = get_session_factory()()
    try:
        version = session.get(DocumentVersion, version_id)
        if version is not None:
            version.parse_status = status
            if status == "FAILED":
                version.error_code = error_code or "KNOWLEDGE_PIPELINE_FAILED"
                version.error_message = (error_message or "知识文档处理失败")[:4_000]
            else:
                version.error_code = None
                version.error_message = None
            session.commit()
            logger.info(f"[{version_id}] parse_status → {status}")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 从 Celery 迁移的分析类任务：同步 service 逻辑包一层 asyncio.to_thread，
# 避免阻塞 ARQ 事件循环导致并发任务串行。
# ---------------------------------------------------------------------------


def _run_match_impl(task_id: str, project_id: str) -> None:
    from uuid import UUID

    from app.core.config import get_settings
    from app.core.errors import DomainError
    from app.db.repositories.identity_repository import IdentityRepository
    from app.db.session import get_session_factory
    from app.services.matching_service import MatchingService
    from app.services.task_service import RUN_MATCH, TaskService

    session = get_session_factory()()
    try:
        task = TaskService(session).start_project_task(UUID(task_id), RUN_MATCH)
        if task is None:
            return
        if task.created_by is None:
            TaskService(session).fail_project_task(
                task.id, RUN_MATCH, "AUTHENTICATION_FAILED", "任务创建者不存在。"
            )
            return
        try:
            roles = IdentityRepository(session).list_role_codes(task.created_by)
            MatchingService(session, get_settings()).run(UUID(project_id), task.created_by, roles)
        except DomainError as exc:
            TaskService(session).fail_project_task(task.id, RUN_MATCH, exc.code, exc.message)
        except Exception:
            logger.exception(f"[run_match] task={task_id} failed")
            TaskService(session).fail_project_task(
                task.id, RUN_MATCH, "MATCH_FAILED", "材料匹配失败，请稍后重新执行。"
            )
        else:
            TaskService(session).complete_project_task(task.id, RUN_MATCH, "材料匹配任务已完成。")
    finally:
        session.close()


async def run_match(ctx: dict[str, Any], task_id: str, project_id: str) -> dict[str, Any]:
    del ctx
    await asyncio.to_thread(_run_match_impl, task_id, project_id)
    return {"status": "done", "task_id": task_id}


def _run_risk_check_impl(task_id: str, project_id: str) -> None:
    from uuid import UUID

    from app.core.errors import DomainError
    from app.db.repositories.identity_repository import IdentityRepository
    from app.db.session import get_session_factory
    from app.services.risk_service import RiskService
    from app.services.task_service import RUN_RISK_CHECK, TaskService

    session = get_session_factory()()
    try:
        task = TaskService(session).start_project_task(UUID(task_id), RUN_RISK_CHECK)
        if task is None:
            return
        if task.created_by is None:
            TaskService(session).fail_project_task(
                task.id, RUN_RISK_CHECK, "AUTHENTICATION_FAILED", "任务创建者不存在。"
            )
            return
        try:
            roles = IdentityRepository(session).list_role_codes(task.created_by)
            RiskService(session).run(UUID(project_id), task.created_by, roles)
        except DomainError as exc:
            TaskService(session).fail_project_task(task.id, RUN_RISK_CHECK, exc.code, exc.message)
        except Exception:
            logger.exception(f"[run_risk_check] task={task_id} failed")
            TaskService(session).fail_project_task(
                task.id,
                RUN_RISK_CHECK,
                "RISK_CHECK_FAILED",
                "风险检查失败，请稍后重新执行。",
            )
        else:
            TaskService(session).complete_project_task(
                task.id, RUN_RISK_CHECK, "风险检查任务已完成。"
            )
    finally:
        session.close()


async def run_risk_check(ctx: dict[str, Any], task_id: str, project_id: str) -> dict[str, Any]:
    del ctx
    await asyncio.to_thread(_run_risk_check_impl, task_id, project_id)
    return {"status": "done", "task_id": task_id}


def _generate_decision_impl(task_id: str, project_id: str) -> None:
    from uuid import UUID

    from app.core.errors import DomainError
    from app.db.repositories.identity_repository import IdentityRepository
    from app.db.session import get_session_factory
    from app.services.decision_service import DecisionService
    from app.services.task_service import RUN_DECISION, TaskService

    session = get_session_factory()()
    try:
        task = TaskService(session).start_project_task(UUID(task_id), RUN_DECISION)
        if task is None:
            return
        if task.created_by is None:
            TaskService(session).fail_project_task(
                task.id, RUN_DECISION, "AUTHENTICATION_FAILED", "任务创建者不存在。"
            )
            return
        try:
            roles = IdentityRepository(session).list_role_codes(task.created_by)
            DecisionService(session).generate(UUID(project_id), task.created_by, roles)
        except DomainError as exc:
            TaskService(session).fail_project_task(task.id, RUN_DECISION, exc.code, exc.message)
        except Exception:
            logger.exception(f"[generate_decision] task={task_id} failed")
            TaskService(session).fail_project_task(
                task.id, RUN_DECISION, "DECISION_FAILED", "投标建议生成失败，请稍后重新执行。"
            )
        else:
            TaskService(session).complete_project_task(task.id, RUN_DECISION, "投标建议已生成。")
    finally:
        session.close()


async def generate_decision(ctx: dict[str, Any], task_id: str, project_id: str) -> dict[str, Any]:
    del ctx
    await asyncio.to_thread(_generate_decision_impl, task_id, project_id)
    return {"status": "done", "task_id": task_id}


def _generate_report_impl(task_id: str, report_id: str) -> None:
    from uuid import UUID

    from app.core.config import get_settings
    from app.core.errors import DomainError
    from app.db.repositories.identity_repository import IdentityRepository
    from app.db.session import get_session_factory
    from app.integrations.object_storage import MinioObjectStorage
    from app.services.report_service import ReportService
    from app.services.task_service import TaskService

    session = get_session_factory()()
    try:
        task = TaskService(session).start_report_task(UUID(task_id))
        if task is None:
            return
        if task.created_by is None:
            TaskService(session).fail_report_task(
                task.id, "AUTHENTICATION_FAILED", "报告任务创建者不存在。"
            )
            return
        service = ReportService(session, MinioObjectStorage(get_settings()))
        try:
            roles = IdentityRepository(session).list_role_codes(task.created_by)
            service.generate(UUID(report_id), task.created_by, roles)
        except DomainError as exc:
            service.mark_failed(UUID(report_id), exc.code, exc.message)
            from app.services.analysis_service import AnalysisService

            AnalysisService.mark_report_terminal(
                UUID(report_id), succeeded=False, error_message=exc.message
            )
            TaskService(session).fail_report_task(task.id, exc.code, exc.message)
        except Exception:
            logger.exception(f"[generate_report] task={task_id} failed")
            message = "报告生成失败，请稍后重新执行。"
            service.mark_failed(UUID(report_id), "REPORT_GENERATION_FAILED", message)
            from app.services.analysis_service import AnalysisService

            AnalysisService.mark_report_terminal(
                UUID(report_id), succeeded=False, error_message=message
            )
            TaskService(session).fail_report_task(task.id, "REPORT_GENERATION_FAILED", message)
        else:
            from app.services.analysis_service import AnalysisService

            AnalysisService.mark_report_terminal(UUID(report_id), succeeded=True)
            TaskService(session).complete_report_task(task.id, "投标综合分析报告已生成。")
    finally:
        session.close()


async def generate_report(ctx: dict[str, Any], task_id: str, report_id: str) -> dict[str, Any]:
    del ctx
    await asyncio.to_thread(_generate_report_impl, task_id, report_id)
    return {"status": "done", "task_id": task_id}


def _run_project_analysis_impl(task_id: str, analysis_run_id: str) -> None:
    from uuid import UUID

    from app.services.analysis_service import AnalysisService

    AnalysisService.execute(UUID(task_id), UUID(analysis_run_id))


async def run_project_analysis(
    ctx: dict[str, Any], task_id: str, analysis_run_id: str
) -> dict[str, Any]:
    del ctx
    await asyncio.to_thread(_run_project_analysis_impl, task_id, analysis_run_id)
    return {"status": "done", "task_id": task_id}


def _run_integration_impl(
    integration_run_id: str, project_id: str, payload: dict[str, object]
) -> None:
    from uuid import UUID

    from app.core.config import get_settings
    from app.db.session import get_session_factory
    from app.integrations.external_connectors import HttpConnectorExecutor
    from app.integrations.object_storage import MinioObjectStorage
    from app.services.advanced_service import AdvancedService

    session = get_session_factory()()
    try:
        settings = get_settings()
        AdvancedService(session, MinioObjectStorage(settings), settings).execute_integration_run(
            UUID(integration_run_id),
            UUID(project_id),
            payload,
            HttpConnectorExecutor(settings),
        )
    finally:
        session.close()


async def run_integration(
    ctx: dict[str, Any], integration_run_id: str, project_id: str, payload: dict[str, object]
) -> dict[str, Any]:
    del ctx
    await asyncio.to_thread(_run_integration_impl, integration_run_id, project_id, payload)
    return {"status": "done", "integration_run_id": integration_run_id}


def _update_version_status(
    version_id: Any, status: str, error_code: str | None = None, error_message: str | None = None
) -> None:
    """pipeline 结束后更新 DocumentVersion.parse_status（READY / FAILED）。"""
    from datetime import UTC, datetime

    from app.db.models import DocumentVersion
    from app.db.session import get_session_factory

    session = get_session_factory()()
    try:
        version = session.get(DocumentVersion, version_id)
        if version is not None:
            version.parse_status = status
            version.error_code = error_code
            version.error_message = error_message
            version.completed_at = datetime.now(UTC) if status != "QUEUED" else None
            session.commit()
            logger.info(f"[{version_id}] parse_status → {status}")
    finally:
        session.close()


def _update_pipeline_task_status(
    version_id: Any,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Keep the user-visible pipeline task terminal state aligned with the ARQ result."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.db.models import Task, TaskEvent
    from app.db.session import get_session_factory

    session = get_session_factory()()
    try:
        task = session.scalar(
            select(Task)
            .where(
                Task.task_type == "PIPELINE_DOCUMENT",
                Task.target_type == "DOCUMENT_VERSION",
                Task.target_id == version_id,
            )
            .order_by(Task.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        if task is None or task.status in {"SUCCEEDED", "CANCELLED"}:
            return
        previous_status = task.status
        task.status = status
        task.error_code = error_code
        task.error_message = error_message
        task.completed_at = datetime.now(UTC)
        session.add(
            TaskEvent(
                task_id=task.id,
                from_status=previous_status,
                to_status=status,
                message=error_message or "LangGraph 流水线任务已完成",
                created_at=task.completed_at,
            )
        )
        session.commit()
    finally:
        session.close()


class WorkerSettings:
    """ARQ Worker 配置"""

    functions = [
        process_im_message,
        run_bid_pipeline,
        run_knowledge_pipeline,
        run_match,
        run_risk_check,
        generate_decision,
        generate_report,
        run_project_analysis,
        run_integration,
    ]
    max_jobs = 3
    keep_result = 3600  # 保留结果 1 小时
    max_tries = 3  # 任务抛异常时的默认重试次数（knowledge_pipeline 等）
    on_startup = configure_worker_logging
    # The ARQ CLI reads this class attribute directly.  A helper method alone
    # is ignored and makes the CLI silently fall back to unauthenticated
    # localhost:6379, while publishers still use the configured Redis URL.
    _redis_url = get_settings().redis_url
    redis_settings = RedisSettings.from_dsn(_redis_url) if _redis_url else None

    @classmethod
    def get_redis_settings(cls) -> RedisSettings:
        settings = get_settings()
        if not settings.redis_url:
            raise RuntimeError("redis_url not configured")
        return RedisSettings.from_dsn(settings.redis_url)


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    worker = Worker(
        functions=WorkerSettings.functions,
        redis_settings=WorkerSettings.get_redis_settings(),
        max_jobs=WorkerSettings.max_jobs,
        keep_result=WorkerSettings.keep_result,
    )
    asyncio.run(worker.run())
