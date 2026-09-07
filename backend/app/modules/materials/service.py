# ruff: noqa: E501
"""企业材料业务规则。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.analysis.invalidation_service import AnalysisInvalidationService
from app.modules.identity.service import AuthenticatedUser
from app.modules.materials.models import EnterpriseMaterial
from app.modules.materials.repository import MaterialRepository
from app.modules.materials.schemas import MaterialUpsertRequest
from app.modules.projects.models import Enterprise
from app.modules.projects.repository import ProjectRepository


class MaterialService:
    """材料事实由企业管理员维护，不按请求参数伪造企业范围。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._materials = MaterialRepository(session)
        self._projects = ProjectRepository(session)
        self._invalidation = AnalysisInvalidationService(session)

    async def list(
        self, actor: AuthenticatedUser, enterprise_id: UUID | None = None
    ) -> list[EnterpriseMaterial]:
        self._require_manage(actor)
        return await self._materials.list_materials(enterprise_id)

    async def create(
        self, actor: AuthenticatedUser, payload: MaterialUpsertRequest
    ) -> EnterpriseMaterial:
        self._require_manage(actor)
        await self._require_active_enterprise(payload.enterprise_id)
        now = datetime.now(UTC)
        material = EnterpriseMaterial(
            id=uuid4(),
            status="DRAFT",
            created_at=now,
            created_by=actor.id,
            updated_at=now,
            updated_by=actor.id,
            **payload.model_dump(),
        )
        self._materials.add_material(material)
        return material

    async def update(
        self, material_id: UUID, actor: AuthenticatedUser, payload: MaterialUpsertRequest
    ) -> EnterpriseMaterial:
        self._require_manage(actor)
        material = await self._require_material(material_id, for_update=True)
        previous_enterprise_id = material.enterprise_id
        was_confirmed = material.status == "CONFIRMED"
        if material.status == "ARCHIVED":
            raise DomainError("INVALID_STATE_TRANSITION", "归档材料不能修改", 409)
        if payload.enterprise_id != material.enterprise_id:
            await self._require_active_enterprise(payload.enterprise_id)
        for field, value in payload.model_dump().items():
            setattr(material, field, value)
        material.updated_at, material.updated_by = datetime.now(UTC), actor.id
        if was_confirmed:
            await self._invalidate_projects_for_enterprises({previous_enterprise_id, material.enterprise_id})
        return material

    async def confirm(self, material_id: UUID, actor: AuthenticatedUser) -> EnterpriseMaterial:
        """确认基础演示材料；本项目不采集也不解析企业证明文件。"""
        self._require_manage(actor)
        material = await self._require_material(material_id, for_update=True)
        material.status = "CONFIRMED"
        material.updated_at = datetime.now(UTC)
        material.updated_by = actor.id
        await self._invalidate_projects_for_enterprises({material.enterprise_id})
        return material

    async def archive(self, material_id: UUID, actor: AuthenticatedUser) -> EnterpriseMaterial:
        """归档材料保留审计事实，但后续匹配不会读取。"""
        self._require_manage(actor)
        material = await self._require_material(material_id, for_update=True)
        material.status = "ARCHIVED"
        material.updated_at = datetime.now(UTC)
        material.updated_by = actor.id
        await self._invalidate_projects_for_enterprises({material.enterprise_id})
        return material

    async def _invalidate_projects_for_enterprises(self, enterprise_ids: set[UUID]) -> None:
        project_ids: set[UUID] = set()
        for enterprise_id in enterprise_ids:
            project_ids.update(
                await self._projects.list_active_project_ids_for_enterprise(enterprise_id)
            )
        await self._invalidation.invalidate_inputs(project_ids)

    @staticmethod
    def _require_manage(actor: AuthenticatedUser) -> None:
        if "SYSTEM_ADMIN" not in actor.role_codes and "BID_SPECIALIST" not in actor.role_codes:
            raise DomainError("PERMISSION_DENIED", "无权维护企业材料", 403)

    async def _require_material(self, material_id: UUID, *, for_update: bool) -> EnterpriseMaterial:
        material = await self._materials.get_material(material_id, for_update=for_update)
        if material is None:
            raise DomainError("RESOURCE_NOT_FOUND", "企业材料不存在", 404)
        return material

    async def _require_active_enterprise(self, enterprise_id: UUID) -> None:
        enterprise = await self._session.get(Enterprise, enterprise_id)
        if enterprise is None or enterprise.deleted_at is not None or enterprise.status != "ACTIVE":
            raise DomainError("ENTERPRISE_NOT_FOUND", "材料归属企业不存在或已停用", 422)
