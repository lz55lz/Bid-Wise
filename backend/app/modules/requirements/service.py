# ruff: noqa: E501
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.analysis.invalidation_service import AnalysisInvalidationService
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService
from app.modules.requirements.models import ProjectField, TenderRequirement
from app.modules.requirements.repository import RequirementRepository


class RequirementService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._requirements = RequirementRepository(session)
        self._invalidation = AnalysisInvalidationService(session)

    async def list_for_project(
        self, project_id: UUID, actor: AuthenticatedUser
    ) -> list[TenderRequirement]:
        await ProjectService(self._session).require_project_access(project_id, actor)
        return await self._requirements.list_active(project_id)

    async def review(
        self,
        project_id: UUID,
        requirement_id: UUID,
        actor: AuthenticatedUser,
        status: str,
        note: str | None,
    ) -> TenderRequirement:
        if status not in {"CONFIRMED", "REJECTED"}:
            raise DomainError("VALIDATION_ERROR", "审核状态只能是 CONFIRMED 或 REJECTED", 422)
        await ProjectService(self._session).require_document_write(project_id, actor)
        requirement = await self._requirements.get(requirement_id, for_update=True)
        if requirement is None or requirement.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "招标需求不存在或无权访问", 404)
        requirement.review_status, requirement.review_note = status, note
        requirement.reviewed_at, requirement.reviewed_by = datetime.now(UTC), actor.id
        requirement.updated_at = datetime.now(UTC)
        await self._invalidation.invalidate_inputs({project_id})
        return requirement

    async def list_fields(self, project_id: UUID, actor: AuthenticatedUser) -> list[ProjectField]:
        await ProjectService(self._session).require_project_access(project_id, actor)
        return await self._requirements.list_fields(project_id)

    async def review_field(
        self,
        project_id: UUID,
        field_id: UUID,
        actor: AuthenticatedUser,
        status: str,
        note: str | None,
    ) -> ProjectField:
        """字段审核与管线 HITL 一致：只确认或拒绝已落库的抽取事实。"""
        if status not in {"CONFIRMED", "REJECTED"}:
            raise DomainError("VALIDATION_ERROR", "审核状态只能是 CONFIRMED 或 REJECTED", 422)
        await ProjectService(self._session).require_document_write(project_id, actor)
        field = await self._requirements.get_field(field_id, for_update=True)
        if field is None or field.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "项目字段不存在或无权访问", 404)
        now = datetime.now(UTC)
        field.review_status = status
        field.review_note = note
        field.reviewed_at = now
        field.reviewed_by = actor.id
        field.updated_at = now
        await self._invalidation.invalidate_inputs({project_id})
        return field

    async def bulk_review(
        self,
        project_id: UUID,
        requirement_ids: list[UUID],
        actor: AuthenticatedUser,
        status: str,
        note: str | None,
    ) -> list[TenderRequirement]:
        """批量审核先校验全部资源归属，再整体更新，避免部分成功造成审核状态混杂。"""
        if status not in {"CONFIRMED", "REJECTED"}:
            raise DomainError("VALIDATION_ERROR", "审核状态只能是 CONFIRMED 或 REJECTED", 422)
        await ProjectService(self._session).require_document_write(project_id, actor)
        ids = list(dict.fromkeys(requirement_ids))
        if not ids:
            raise DomainError("VALIDATION_ERROR", "至少选择一条招标需求", 422)
        requirements = [
            await self._requirements.get(requirement_id, for_update=True) for requirement_id in ids
        ]
        if any(item is None or item.project_id != project_id for item in requirements):
            raise DomainError("RESOURCE_NOT_FOUND", "招标需求不存在或无权访问", 404)
        now = datetime.now(UTC)
        for requirement in requirements:
            assert requirement is not None  # 已在上方完成统一归属校验。
            requirement.review_status = status
            requirement.review_note = note
            requirement.reviewed_at = now
            requirement.reviewed_by = actor.id
            requirement.updated_at = now
        await self._invalidation.invalidate_inputs({project_id})
        return [item for item in requirements if item is not None]
