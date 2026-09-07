"""企业主体维护服务。

企业只是投标项目和材料的共同归属，不是 tenant；权限仍是系统角色和项目成员关系。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.analysis.invalidation_service import AnalysisInvalidationService
from app.modules.identity.models import AuditLog
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.enterprise_schemas import (
    EnterpriseCreateRequest,
    EnterpriseMemberCreateRequest,
    EnterpriseMemberResponse,
    EnterpriseMemberUpdateRequest,
    EnterpriseResponse,
    EnterpriseUpdateRequest,
)
from app.modules.projects.models import Enterprise, EnterpriseMember
from app.modules.projects.repository import ProjectRepository


class EnterpriseService:
    """集中企业的基础维护规则，仓储不承担角色和状态分支。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectRepository(session)

    async def list(self, actor: AuthenticatedUser) -> list[EnterpriseResponse]:
        self._require_manage(actor)
        enterprises = await self._projects.list_enterprises()
        return [EnterpriseResponse.model_validate(item) for item in enterprises]

    async def create(
        self, actor: AuthenticatedUser, payload: EnterpriseCreateRequest
    ) -> EnterpriseResponse:
        self._require_manage(actor)
        if payload.credit_code and await self._projects.get_by_credit_code(payload.credit_code):
            raise DomainError("ENTERPRISE_CREDIT_CODE_EXISTS", "统一社会信用代码已存在", 409)
        now = datetime.now(UTC)
        enterprise = Enterprise(
            id=uuid4(),
            status="ACTIVE",
            created_at=now,
            created_by=actor.id,
            updated_at=now,
            **payload.model_dump(),
        )
        self._projects.add_enterprise_entity(enterprise)
        self._projects.add_enterprise_member(
            EnterpriseMember(
                id=uuid4(),
                enterprise_id=enterprise.id,
                user_id=actor.id,
                role="ADMIN",
                created_at=now,
                updated_at=now,
            )
        )
        return EnterpriseResponse.model_validate(enterprise)

    async def list_members(
        self, enterprise_id: UUID, actor: AuthenticatedUser
    ) -> list[EnterpriseMemberResponse]:
        await self._require_member_or_manager(enterprise_id, actor)
        return [
            self._member_response(member, user)
            for member, user in await self._projects.list_enterprise_members(enterprise_id)
        ]

    async def add_member(
        self, enterprise_id: UUID, actor: AuthenticatedUser, payload: EnterpriseMemberCreateRequest
    ) -> EnterpriseMemberResponse:
        await self._require_admin(enterprise_id, actor)
        if await self._projects.get_enterprise_member(enterprise_id, payload.user_id):
            raise DomainError("ALREADY_EXISTS", "该用户已是企业成员", 409)
        from app.modules.identity.repository import IdentityRepository

        user = await IdentityRepository(self._session).get_active_user(payload.user_id)
        if user is None:
            raise DomainError("USER_NOT_FOUND", "用户不存在或已禁用", 404)
        now = datetime.now(UTC)
        member = EnterpriseMember(
            id=uuid4(),
            enterprise_id=enterprise_id,
            user_id=user.id,
            role=payload.role,
            created_at=now,
            updated_at=now,
        )
        self._projects.add_enterprise_member(member)
        return self._member_response(member, user)

    async def update_member(
        self,
        enterprise_id: UUID,
        member_id: UUID,
        actor: AuthenticatedUser,
        payload: EnterpriseMemberUpdateRequest,
    ) -> EnterpriseMemberResponse:
        await self._require_admin(enterprise_id, actor)
        member = await self._projects.get_enterprise_member_by_id(enterprise_id, member_id)
        if member is None:
            raise DomainError("RESOURCE_NOT_FOUND", "企业成员不存在", 404)
        member.role, member.updated_at = payload.role, datetime.now(UTC)
        from app.modules.identity.repository import IdentityRepository

        user = await IdentityRepository(self._session).get_user(member.user_id)
        if user is None:
            raise DomainError("USER_NOT_FOUND", "成员账户不存在", 404)
        return self._member_response(member, user)

    async def remove_member(
        self, enterprise_id: UUID, member_id: UUID, actor: AuthenticatedUser
    ) -> None:
        await self._require_admin(enterprise_id, actor)
        member = await self._projects.get_enterprise_member_by_id(enterprise_id, member_id)
        if member is None:
            raise DomainError("RESOURCE_NOT_FOUND", "企业成员不存在", 404)
        if member.user_id == actor.id and member.role == "ADMIN":
            raise DomainError("ADMIN_ROLE_PROTECTED", "不能移除当前企业管理员", 409)
        await self._session.delete(member)

    async def get(self, enterprise_id: UUID, actor: AuthenticatedUser) -> EnterpriseResponse:
        """供项目创建页和材料库回显企业信息；不暴露已软删除主体。"""
        self._require_manage(actor)
        enterprise = await self._projects.get_enterprise(enterprise_id)
        if enterprise is None:
            raise DomainError("RESOURCE_NOT_FOUND", "企业不存在", 404)
        return EnterpriseResponse.model_validate(enterprise)

    async def update(
        self, enterprise_id: UUID, actor: AuthenticatedUser, payload: EnterpriseUpdateRequest
    ) -> EnterpriseResponse:
        self._require_manage(actor)
        enterprise = await self._projects.get_enterprise(enterprise_id, for_update=True)
        if enterprise is None:
            raise DomainError("RESOURCE_NOT_FOUND", "企业不存在", 404)
        if payload.credit_code and payload.credit_code != enterprise.credit_code:
            existing = await self._projects.get_by_credit_code(payload.credit_code)
            if existing is not None:
                raise DomainError("ENTERPRISE_CREDIT_CODE_EXISTS", "统一社会信用代码已存在", 409)
        values = payload.model_dump(exclude_unset=True)
        project_ids = set(
            await self._projects.list_active_project_ids_for_enterprise(enterprise.id)
        )
        if values.get("status") == "DISABLED" and project_ids:
            raise DomainError(
                "ENTERPRISE_IN_USE",
                "企业仍绑定在活动项目中，请先在相关项目中更换或移除该企业",
                409,
            )
        for field, value in values.items():
            setattr(enterprise, field, value)
        enterprise.updated_at = datetime.now(UTC)
        await AnalysisInvalidationService(self._session).invalidate_report(project_ids)
        return EnterpriseResponse.model_validate(enterprise)

    async def delete(self, enterprise_id: UUID, actor: AuthenticatedUser) -> None:
        """软删除未被项目使用的企业，保留历史材料和审计记录。"""
        self._require_manage(actor)
        enterprise = await self._projects.get_enterprise(enterprise_id, for_update=True)
        if enterprise is None:
            raise DomainError("RESOURCE_NOT_FOUND", "企业不存在", 404)
        if await self._projects.has_active_project_binding(enterprise_id):
            raise DomainError(
                "ENTERPRISE_IN_USE",
                "企业仍绑定在项目中，请先在相关项目中更换或移除该企业",
                409,
            )
        now = datetime.now(UTC)
        enterprise.status = "ARCHIVED"
        enterprise.deleted_at = now
        enterprise.updated_at = now
        self._session.add(
            AuditLog(
                actor_id=actor.id,
                action="DELETE_ENTERPRISE",
                target_type="ENTERPRISE",
                target_id=enterprise.id,
                project_id=None,
                before_summary=f"企业：{enterprise.name}",
                after_summary="已软删除",
                created_at=now,
            )
        )

    @staticmethod
    def _require_manage(actor: AuthenticatedUser) -> None:
        if not {"SYSTEM_ADMIN", "BID_SPECIALIST"}.intersection(actor.role_codes):
            raise DomainError("PERMISSION_DENIED", "无权维护投标企业", 403)

    async def _require_member_or_manager(
        self, enterprise_id: UUID, actor: AuthenticatedUser
    ) -> None:
        if {"SYSTEM_ADMIN", "BID_SPECIALIST"}.intersection(actor.role_codes):
            return
        if await self._projects.get_enterprise_member(enterprise_id, actor.id) is None:
            raise DomainError("PERMISSION_DENIED", "无权访问企业成员", 403)

    async def _require_admin(self, enterprise_id: UUID, actor: AuthenticatedUser) -> None:
        if {"SYSTEM_ADMIN", "BID_SPECIALIST"}.intersection(actor.role_codes):
            return
        member = await self._projects.get_enterprise_member(enterprise_id, actor.id)
        if member is None or member.role != "ADMIN":
            raise DomainError("PERMISSION_DENIED", "仅企业管理员可维护成员", 403)

    @staticmethod
    def _member_response(member: EnterpriseMember, user) -> EnterpriseMemberResponse:
        return EnterpriseMemberResponse(
            id=member.id,
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=member.role,
            created_at=member.created_at,
            updated_at=member.updated_at,
        )
