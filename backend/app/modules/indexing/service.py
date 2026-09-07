"""Evidence 向量索引任务的提交、查询和 Worker 执行服务。"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.integrations.embedding import BgeM3EmbeddingClient, EmbeddingUnavailable
from app.integrations.task_queue import ArqTaskQueue, TaskQueueUnavailable
from app.modules.evidence.repository import EvidenceRepository
from app.modules.identity.models import AuditLog
from app.modules.identity.service import AuthenticatedUser
from app.modules.indexing.models import EvidenceIndexJob
from app.modules.indexing.repository import EvidenceIndexJobRepository
from app.modules.indexing.schemas import EvidenceIndexJobResponse
from app.modules.indexing.state_machine import transition
from app.modules.projects.service import ProjectService
from app.modules.retrieval.models import EvidenceEmbedding

logger = logging.getLogger(__name__)

class EvidenceIndexingService:
    """把耗时向量生成移出 HTTP 请求，并以 PostgreSQL 状态机保证可观察性。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._jobs = EvidenceIndexJobRepository(session)
        self._evidences = EvidenceRepository(session)
        self._projects = ProjectService(session)

    async def submit(self, project_id: UUID, actor: AuthenticatedUser) -> EvidenceIndexJobResponse:
        """冻结待索引 ID 后发布任务；同一项目同一时间只允许一个运行态任务。"""
        await self._projects.require_project_management(project_id, actor)
        if not self._settings.embedding_is_configured:
            raise DomainError("EMBEDDING_NOT_CONFIGURED", "bge-m3 服务尚未完成部署配置", 503)
        if await self._jobs.get_active(project_id):
            raise DomainError("EVIDENCE_INDEX_ALREADY_RUNNING", "该项目已有正在执行的索引任务", 409)

        evidences = await self._evidences.list_unindexed_by_project(project_id)
        now = datetime.now(UTC)
        job = EvidenceIndexJob(
            project_id=project_id,
            requested_by=actor.id,
            status="QUEUED",
            evidence_ids=[str(item.id) for item in evidences],
            requested_count=len(evidences),
            indexed_count=0,
            created_at=now,
        )
        self._jobs.add(job)
        self._session.add(
            AuditLog(
                actor_id=actor.id,
                action="SUBMIT_EVIDENCE_INDEX",
                target_type="EVIDENCE_INDEX_JOB",
                target_id=job.id,
                project_id=project_id,
                after_summary=f"冻结 {len(evidences)} 条 Evidence 索引输入",
                created_at=now,
            )
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            # PostgreSQL 的部分唯一索引是并发下的最终裁决，预检查仅改善普通提示。
            await self._session.rollback()
            raise DomainError(
                "EVIDENCE_INDEX_ALREADY_RUNNING", "该项目已有正在执行的索引任务", 409
            ) from exc

        if not evidences:
            # 空输入不必进入 Redis；仍保留成功任务，调用方可获得一致的异步响应结构。
            transition(job, "SUCCEEDED")
            job.completed_at = now
            await self._session.commit()
            return self._response(job)
        try:
            await ArqTaskQueue(self._settings).publish_evidence_index(str(job.id))
        except TaskQueueUnavailable:
            # 保持 QUEUED，周期 reconciler 会安全补投固定 job_id。
            logger.warning(
                "ARQ 暂不可用，Evidence 索引任务等待 reconciler 补投 job_id=%s",
                job.id,
            )
        return self._response(job)

    async def get(
        self, project_id: UUID, job_id: UUID, actor: AuthenticatedUser
    ) -> EvidenceIndexJobResponse:
        """任务 ID 不是授权依据，先校验成员资格再确认任务项目归属。"""
        await self._projects.require_project_access(project_id, actor)
        job = await self._jobs.get(job_id)
        if job is None or job.project_id != project_id:
            raise DomainError("EVIDENCE_INDEX_NOT_FOUND", "索引任务不存在或无权访问", 404)
        return self._response(job)

    async def process(self, job_id: UUID) -> None:
        """Worker 从数据库回查任务和 Evidence；重试不会信任旧队列载荷。"""
        job = await self._jobs.get(job_id, for_update=True)
        if job is None or job.status != "QUEUED":
            return
        transition(job, "RUNNING")
        job.started_at = datetime.now(UTC)
        job.error_code = None
        job.error_message = None
        await self._session.commit()
        try:
            evidence_ids = [UUID(value) for value in job.evidence_ids]
            evidences = await self._evidences.list_unindexed_by_ids(job.project_id, evidence_ids)
            vectors = await BgeM3EmbeddingClient(self._settings).embed(
                [self._embedding_text(item) for item in evidences]
            )
            now = datetime.now(UTC)
            self._session.add_all(
                EvidenceEmbedding(evidence_id=item.id, embedding=vector, indexed_at=now)
                for item, vector in zip(evidences, vectors, strict=True)
            )
            job.indexed_count = len(evidences)
            transition(job, "SUCCEEDED")
            job.completed_at = now
            self._session.add(
                AuditLog(
                    actor_id=job.requested_by,
                    action="COMPLETE_EVIDENCE_INDEX",
                    target_type="EVIDENCE_INDEX_JOB",
                    target_id=job.id,
                    project_id=job.project_id,
                    after_summary=f"新增 {len(evidences)} 条 Evidence 向量",
                    created_at=now,
                )
            )
            await self._session.commit()
        except EmbeddingUnavailable as exc:
            await self._session.rollback()
            await self._mark_failed(job_id, "EMBEDDING_UNAVAILABLE", "向量服务暂不可用")
            raise exc
        except Exception:
            await self._session.rollback()
            await self._mark_failed(
                job_id, "EVIDENCE_INDEX_FAILED", "Evidence 索引失败，请稍后重试"
            )
            raise

    async def _mark_failed(self, job_id: UUID, code: str, message: str) -> None:
        """事务异常后单独持久化失败状态，避免前端无限轮询运行中任务。"""
        job = await self._jobs.get(job_id, for_update=True)
        if job is None:
            return
        transition(job, "FAILED")
        job.error_code = code
        job.error_message = message
        job.completed_at = datetime.now(UTC)
        await self._session.commit()

    @staticmethod
    def _embedding_text(evidence) -> str:
        """重建索引与首次解析使用同一检索语义：来源/章节只增强向量，不污染引用原文。"""
        locator = evidence.locator if isinstance(evidence.locator, dict) else {}
        prefixes = [
            str(locator.get(key) or "").strip()
            for key in ("document_name", "section_path")
        ]
        prefix = " / ".join(dict.fromkeys(item for item in prefixes if item))
        body = str(locator.get("retrieval_text") or evidence.quoted_text or "").strip()
        text = f"{prefix}\n{body}" if prefix else body
        return text[:8_000]

    @staticmethod
    def _response(job: EvidenceIndexJob) -> EvidenceIndexJobResponse:
        return EvidenceIndexJobResponse(
            id=job.id,
            project_id=job.project_id,
            status=job.status,
            requested_count=job.requested_count,
            indexed_count=job.indexed_count,
            error_code=job.error_code,
            error_message=job.error_message,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
