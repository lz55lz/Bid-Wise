"""Evidence 查询用例：授权与资源归属必须同时满足。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.evidence.repository import EvidenceRepository
from app.modules.evidence.schemas import EvidenceResponse
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService


class EvidenceService:
    """不把向量元数据、对象键或 Evidence UUID 当作授权凭据。"""

    def __init__(self, session: AsyncSession) -> None:
        self._projects = ProjectService(session)
        self._evidences = EvidenceRepository(session)

    async def get(
        self, project_id: UUID, evidence_id: UUID, actor: AuthenticatedUser
    ) -> EvidenceResponse:
        await self._projects.require_project_access(project_id, actor)
        evidence = await self._evidences.get_in_project(project_id, evidence_id)
        if evidence is None:
            # 对无权项目和不存在 Evidence 都不暴露额外信息。
            raise DomainError("EVIDENCE_NOT_FOUND", "证据不存在或无权访问", 404)
        return EvidenceResponse(
            id=evidence.id,
            project_id=evidence.project_id,
            source_type=evidence.source_type,
            document_version_id=evidence.document_version_id,
            document_node_id=evidence.document_node_id,
            quoted_text=evidence.quoted_text,
            locator=evidence.locator,
            created_at=evidence.created_at,
        )
