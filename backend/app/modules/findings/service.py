"""发现项应用服务：证据锚定、人工复核与审计。"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.analysis.invalidation_service import AnalysisInvalidationService
from app.modules.evidence.repository import EvidenceRepository
from app.modules.findings.models import ProjectFinding
from app.modules.findings.repository import FindingRepository
from app.modules.findings.schemas import FindingCreateRequest, FindingResponse, FindingReviewRequest
from app.modules.identity.models import AuditLog
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService


class FindingService:
    """发现项不直接依赖 LLM；后续 AI Worker 也复用同一证据与复核规则。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._findings = FindingRepository(session)
        self._evidences = EvidenceRepository(session)
        self._projects = ProjectService(session)
        self._invalidation = AnalysisInvalidationService(session)

    async def create(
        self, project_id: UUID, actor: AuthenticatedUser, payload: FindingCreateRequest
    ) -> FindingResponse:
        """编辑者可提交候选，系统始终以待复核状态保存。"""
        await self._projects.require_document_write(project_id, actor)
        evidence_ids = list(dict.fromkeys(payload.evidence_ids))
        evidences = await self._evidences.list_current_by_ids_in_project(project_id, evidence_ids)
        if len(evidences) != len(evidence_ids):
            raise DomainError("EVIDENCE_NOT_FOUND", "存在无效或无权关联的 Evidence", 422)
        now = datetime.now(UTC)
        finding = ProjectFinding(
            project_id=project_id,
            kind=payload.kind,
            title=payload.title,
            description=payload.description,
            severity=payload.severity,
            status="PENDING_REVIEW",
            source="MANUAL",
            created_by=actor.id,
            created_at=now,
            updated_at=now,
        )
        self._findings.add(finding)
        await self._session.flush()
        self._findings.add_evidence_links(finding.id, evidence_ids)
        self._audit(actor.id, "CREATE_FINDING", finding.id, project_id)
        return self._response(finding, evidence_ids)

    async def list_project(
        self, project_id: UUID, actor: AuthenticatedUser, status: str | None
    ) -> list[FindingResponse]:
        """任何项目成员可查看，但过滤条件严格限制为领域状态。"""
        await self._projects.require_project_access(project_id, actor)
        allowed_statuses = {"PENDING_REVIEW", "CONFIRMED", "DISMISSED", "STALE"}
        if status is not None and status not in allowed_statuses:
            raise DomainError("VALIDATION_ERROR", "发现项状态筛选值无效", 422)
        findings = await self._findings.list_in_project(project_id, status)
        return [
            self._response(finding, await self._findings.list_evidence_ids(finding.id))
            for finding in findings
        ]

    async def review(
        self,
        project_id: UUID,
        finding_id: UUID,
        actor: AuthenticatedUser,
        payload: FindingReviewRequest,
    ) -> FindingResponse:
        """负责人或管理员确认/驳回候选，是正式的人工介入点。"""
        await self._projects.require_project_management(project_id, actor)
        finding = await self._findings.get_in_project(project_id, finding_id)
        if finding is None:
            raise DomainError("FINDING_NOT_FOUND", "发现项不存在或无权访问", 404)
        if finding.status != "PENDING_REVIEW":
            raise DomainError("FINDING_REVIEWED", "发现项已完成复核，不能重复操作", 409)
        now = datetime.now(UTC)
        finding.status = payload.status
        finding.review_note = payload.review_note
        finding.reviewed_by = actor.id
        finding.reviewed_at = now
        finding.updated_at = now
        evidence_ids = await self._findings.list_evidence_ids(finding.id)
        await self._invalidation.invalidate_report({project_id})
        self._audit(actor.id, f"{payload.status}_FINDING", finding.id, project_id)
        return self._response(finding, evidence_ids)

    def _audit(self, actor_id: UUID, action: str, finding_id: UUID, project_id: UUID) -> None:
        """只记录动作和资源 ID，不把发现项正文复制进审计日志。"""
        self._session.add(
            AuditLog(
                actor_id=actor_id,
                action=action,
                target_type="PROJECT_FINDING",
                target_id=finding_id,
                project_id=project_id,
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _response(finding: ProjectFinding, evidence_ids: list[UUID]) -> FindingResponse:
        return FindingResponse(
            id=finding.id,
            project_id=finding.project_id,
            kind=finding.kind,
            title=finding.title,
            description=finding.description,
            severity=finding.severity,
            status=finding.status,
            source=finding.source,
            evidence_ids=evidence_ids,
            created_by=finding.created_by,
            reviewed_by=finding.reviewed_by,
            reviewed_at=finding.reviewed_at,
            review_note=finding.review_note,
            created_at=finding.created_at,
            updated_at=finding.updated_at,
        )


class FindingInvalidationService:
    """处理源 Evidence 失效后的结论状态，不参与 HTTP 授权或模型调用。"""

    def __init__(self, session: AsyncSession) -> None:
        self._findings = FindingRepository(session)
        self._invalidation = AnalysisInvalidationService(session)

    async def mark_stale_for_evidences(self, evidence_ids: list[UUID]) -> int:
        """旧版本 Evidence 退出当前业务视图时，使关联结论失效并保留审计血缘。"""
        findings = await self._findings.list_requiring_stale_mark(evidence_ids)
        now = datetime.now(UTC)
        for finding in findings:
            finding.status = "STALE"
            finding.updated_at = now
        await self._invalidation.invalidate_report({finding.project_id for finding in findings})
        return len(findings)
