"""发现项分析任务的提交和 Worker 编排服务。"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.integrations.llm import LlmUnavailable, MiniMaxM3Client
from app.integrations.task_queue import ArqTaskQueue, TaskQueueUnavailable
from app.modules.analysis.models import FindingAnalysisJob
from app.modules.analysis.repository import FindingAnalysisRepository
from app.modules.analysis.schemas import FindingAnalysisJobResponse
from app.modules.analysis.state_machine import transition_finding
from app.modules.evidence.repository import EvidenceRepository
from app.modules.findings.models import ProjectFinding
from app.modules.findings.repository import FindingRepository
from app.modules.identity.models import AuditLog
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService

logger = logging.getLogger(__name__)

_ALLOWED_KINDS = {"REQUIREMENT", "RISK", "RECOMMENDATION"}
_ALLOWED_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


class FindingAnalysisService:
    """分析任务的输入冻结与输出落库，LLM 不直接接触 ORM 写权限。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._jobs = FindingAnalysisRepository(session)
        self._evidences = EvidenceRepository(session)
        self._findings = FindingRepository(session)
        self._projects = ProjectService(session)

    async def submit(
        self, project_id: UUID, actor: AuthenticatedUser
    ) -> FindingAnalysisJobResponse:
        """冻结当前项目 Evidence 并发布异步任务，禁止并发分析同一项目。"""
        await self._projects.require_project_management(project_id, actor)
        if not self._settings.llm_is_configured:
            raise DomainError("AI_NOT_CONFIGURED", "MiniMax-M3 服务尚未完成部署配置", 503)
        evidences = await self._evidences.list_for_finding_analysis(project_id)
        snapshot = self._snapshot(evidences)
        if not snapshot:
            raise DomainError("ANALYSIS_INPUT_NOT_READY", "请先解析项目文档并生成 Evidence", 409)
        input_hash = self._snapshot_hash(snapshot)
        reusable = await self._jobs.get_reusable(project_id, input_hash)
        if reusable is not None:
            return self._response(reusable)
        if await self._jobs.get_active(project_id):
            raise DomainError("ANALYSIS_ALREADY_RUNNING", "该项目已有正在执行的分析任务", 409)
        now = datetime.now(UTC)
        job = FindingAnalysisJob(
            project_id=project_id,
            requested_by=actor.id,
            status="QUEUED",
            input_hash=input_hash,
            input_snapshot=snapshot,
            created_count=0,
            created_at=now,
        )
        self._jobs.add(job)
        self._session.add(
            AuditLog(
                actor_id=actor.id,
                action="SUBMIT_FINDING_ANALYSIS",
                target_type="FINDING_ANALYSIS_JOB",
                target_id=job.id,
                project_id=project_id,
                created_at=now,
            )
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            # 部分唯一索引是最终并发裁决；预检查只为了给普通请求更快的反馈。
            await self._session.rollback()
            raise DomainError(
                "ANALYSIS_ALREADY_RUNNING", "该项目已有正在执行的分析任务", 409
            ) from exc
        try:
            await ArqTaskQueue(self._settings).publish_finding_analysis(str(job.id))
        except TaskQueueUnavailable:
            # PostgreSQL 中的 QUEUED 任务由 reconciler 补投，不把瞬时 Redis 故障升级为业务失败。
            logger.warning(
                "ARQ 暂不可用，发现项分析等待 reconciler 补投 job_id=%s",
                job.id,
            )
        return self._response(job)

    async def get(
        self, project_id: UUID, job_id: UUID, actor: AuthenticatedUser
    ) -> FindingAnalysisJobResponse:
        """返回任务状态前验证项目成员资格和任务归属。"""
        await self._projects.require_project_access(project_id, actor)
        job = await self._jobs.get(job_id)
        if job is None or job.project_id != project_id:
            raise DomainError("ANALYSIS_NOT_FOUND", "分析任务不存在或无权访问", 404)
        return self._response(job)

    async def process(self, job_id: UUID) -> None:
        """Worker 执行入口：仅接收任务 ID，完整输入和状态从 PostgreSQL 回查。"""
        job = await self._jobs.get(job_id, for_update=True)
        if job is None or job.status != "QUEUED":
            return
        transition_finding(job, "RUNNING")
        job.started_at = datetime.now(UTC)
        job.error_code = None
        job.error_message = None
        await self._session.commit()
        try:
            current_snapshot = self._snapshot(
                await self._evidences.list_for_finding_analysis(job.project_id)
            )
            if self._snapshot_hash(current_snapshot) != job.input_hash:
                await self._mark_failed(
                    job_id,
                    "ANALYSIS_INPUT_CHANGED",
                    "项目 Evidence 已发生变化，请基于当前文档重新发起发现项分析",
                )
                return
            candidates = await MiniMaxM3Client(self._settings).extract_findings(job.input_snapshot)
            created_count = await self._create_candidates(job, candidates)
            transition_finding(job, "SUCCEEDED")
            job.created_count = created_count
            job.completed_at = datetime.now(UTC)
            self._session.add(
                AuditLog(
                    actor_id=job.requested_by,
                    action="COMPLETE_FINDING_ANALYSIS",
                    target_type="FINDING_ANALYSIS_JOB",
                    target_id=job.id,
                    project_id=job.project_id,
                    after_summary=f"创建 {created_count} 条待复核发现项",
                    created_at=job.completed_at,
                )
            )
            await self._session.commit()
        except LlmUnavailable as exc:
            await self._session.rollback()
            await self._mark_failed(job_id, "LLM_UNAVAILABLE", "发现项分析模型暂不可用")
            raise exc
        except Exception:
            await self._session.rollback()
            await self._mark_failed(job_id, "FINDING_ANALYSIS_FAILED", "发现项分析失败，请稍后重试")
            raise

    async def _create_candidates(
        self, job: FindingAnalysisJob, candidates: list[dict[str, object]]
    ) -> int:
        """验证模型字段和 Evidence ID 后再写入，任何模型输出都不是可信业务数据。"""
        valid_ids = {str(item["evidence_id"]) for item in job.input_snapshot}
        created_count = 0
        for candidate in candidates:
            kind = str(candidate.get("kind") or "")
            severity = str(candidate.get("severity") or "")
            title = str(candidate.get("title") or "").strip()
            description = str(candidate.get("description") or "").strip()
            raw_ids = candidate.get("evidence_ids")
            if (
                kind not in _ALLOWED_KINDS
                or severity not in _ALLOWED_SEVERITIES
                or not title
                or not description
                or not isinstance(raw_ids, list)
            ):
                continue
            evidence_ids = [UUID(str(value)) for value in raw_ids if str(value) in valid_ids]
            evidence_ids = list(dict.fromkeys(evidence_ids))
            if not evidence_ids:
                continue
            existing = await self._evidences.list_current_by_ids_in_project(
                job.project_id, evidence_ids
            )
            existing_ids = {item.id for item in existing}
            evidence_ids = [item for item in evidence_ids if item in existing_ids]
            if not evidence_ids:
                continue
            now = datetime.now(UTC)
            finding = ProjectFinding(
                project_id=job.project_id,
                kind=kind,
                title=title[:512],
                description=description[:20_000],
                severity=severity,
                status="PENDING_REVIEW",
                source="AI",
                created_by=job.requested_by,
                created_at=now,
                updated_at=now,
            )
            self._findings.add(finding)
            await self._session.flush()
            self._findings.add_evidence_links(finding.id, evidence_ids)
            created_count += 1
        return created_count

    @staticmethod
    def _snapshot(evidences) -> list[dict[str, object]]:
        return [
            {
                "evidence_id": str(item.id),
                "content": (item.quoted_text or "")[:2_400],
                "locator": item.locator,
            }
            for item in evidences
            if (item.quoted_text or "").strip()
        ]

    @staticmethod
    def _snapshot_hash(snapshot: list[dict[str, object]]) -> str:
        return hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    async def _mark_failed(self, job_id: UUID, code: str, message: str) -> None:
        """事务回滚后单独保存失败终态，避免任务永久显示运行中。"""
        job = await self._jobs.get(job_id, for_update=True)
        if job is None:
            return
        transition_finding(job, "FAILED")
        job.error_code = code
        job.error_message = message
        job.completed_at = datetime.now(UTC)
        await self._session.commit()

    @staticmethod
    def _response(job: FindingAnalysisJob) -> FindingAnalysisJobResponse:
        return FindingAnalysisJobResponse(
            id=job.id,
            project_id=job.project_id,
            status=job.status,
            created_count=job.created_count,
            error_code=job.error_code,
            error_message=job.error_message,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
