"""项目域应用服务：集中项目生命周期和资源授权规则。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.analysis.invalidation_service import AnalysisInvalidationService
from app.modules.identity.models import AuditLog
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.models import ProjectEnterprise, ProjectMember, TenderProject
from app.modules.projects.repository import ProjectRepository
from app.modules.projects.schemas import (
    ProjectAssignableUserResponse,
    ProjectCreateRequest,
    ProjectMemberResponse,
    ProjectMemberUpsertRequest,
    ProjectResponse,
    ProjectUpdateRequest,
)

SYSTEM_ADMIN = "SYSTEM_ADMIN"
BID_SPECIALIST = "BID_SPECIALIST"
PROJECT_OWNER = "OWNER"


class ProjectService:
    """所有项目接口和 Worker 都通过本服务访问项目数据。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._projects = ProjectRepository(session)

    async def create(
        self,
        actor: AuthenticatedUser,
        payload: ProjectCreateRequest,
    ) -> ProjectResponse:
        """创建项目并在同一事务中写入负责人 OWNER 成员关系。"""
        if not {SYSTEM_ADMIN, BID_SPECIALIST}.intersection(actor.role_codes):
            raise DomainError("PERMISSION_DENIED", "无权创建项目", 403)
        code = await self._resolve_code(payload.code)
        enterprise_ids = list(dict.fromkeys(payload.enterprise_ids))
        enterprises = await self._projects.get_active_enterprises(enterprise_ids)
        if len(enterprises) != len(enterprise_ids):
            raise DomainError("ENTERPRISE_NOT_FOUND", "所选投标企业不存在或已停用", 422)
        now = datetime.now(UTC)
        project = TenderProject(
            code=code,
            name=payload.name,
            purchaser=payload.purchaser,
            project_type=payload.project_type,
            region=payload.region,
            bid_deadline=payload.bid_deadline,
            bid_deadline_source="MANUAL" if payload.bid_deadline is not None else None,
            status="DRAFT",
            owner_id=actor.id,
            created_at=now,
            updated_at=now,
        )
        self._projects.add(project)
        await self._session.flush()
        self._projects.add_member(
            ProjectMember(
                project_id=project.id,
                user_id=actor.id,
                role=PROJECT_OWNER,
                created_at=now,
                updated_at=now,
            )
        )
        for index, enterprise_id in enumerate(enterprise_ids):
            self._projects.add_enterprise(
                ProjectEnterprise(
                    project_id=project.id,
                    enterprise_id=enterprise_id,
                    is_lead=index == 0,
                    created_at=now,
                    created_by=actor.id,
                )
            )
        self._record_audit(actor.id, "CREATE_PROJECT", project.id, project.id)
        return self._to_response(project, enterprise_ids)

    async def list_visible(self, actor: AuthenticatedUser) -> list[ProjectResponse]:
        """返回当前用户有权访问的项目，普通用户不会获知其他项目存在。"""
        projects = await self._projects.list_visible(actor.id, self._is_system_admin(actor))
        enterprise_ids_by_project = await self._projects.list_enterprise_ids_for_projects(
            [project.id for project in projects]
        )
        return [
            self._to_response(project, enterprise_ids_by_project.get(project.id, []))
            for project in projects
        ]

    async def get_visible(self, project_id: UUID, actor: AuthenticatedUser) -> ProjectResponse:
        """获取项目详情前先做成员回查，不以 URL、对象键等信息授权。"""
        project, _ = await self._get_authorized_project(project_id, actor)
        return self._to_response(project, await self._projects.list_enterprise_ids(project.id))

    async def require_project_access(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
    ) -> None:
        """供文档、Evidence、报告等下游资源统一复用项目成员授权校验。"""
        await self._get_authorized_project(project_id, actor)

    async def require_document_write(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
    ) -> None:
        """项目文件写入仅允许系统管理员、项目 OWNER 或 EDITOR。"""
        project, member = await self._get_authorized_project(project_id, actor)
        self._require_writable(project)
        if not self._is_system_admin(actor) and (member is None or member.role == "VIEWER"):
            raise DomainError("PERMISSION_DENIED", "无权上传项目文件", 403)

    async def require_project_management(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
    ) -> None:
        """索引重建等会消耗资源的项目操作仅允许 OWNER 或系统管理员。"""
        _, member = await self._get_authorized_project(project_id, actor)
        self._require_owner_or_admin(actor, member)

    async def update(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
        payload: ProjectUpdateRequest,
    ) -> ProjectResponse:
        """只有项目 OWNER 或系统管理员能修改项目基础信息。"""
        project, member = await self._get_authorized_project(project_id, actor)
        self._require_owner_or_admin(actor, member)
        self._require_writable(project)
        before = {
            field: (
                await self._projects.list_enterprise_ids(project.id)
                if field == "enterprise_ids"
                else getattr(project, field)
            )
            for field in payload.model_fields_set
        }
        values = payload.model_dump(exclude_unset=True)
        enterprise_ids = values.pop("enterprise_ids", None)
        if enterprise_ids is not None:
            enterprise_ids = list(dict.fromkeys(enterprise_ids))
            enterprises = await self._projects.get_active_enterprises(enterprise_ids)
            if len(enterprises) != len(enterprise_ids):
                raise DomainError("ENTERPRISE_NOT_FOUND", "所选投标企业不存在或已停用", 422)
        for field, value in values.items():
            setattr(project, field, value)
        if "bid_deadline" in payload.model_fields_set:
            project.bid_deadline_source = "MANUAL"
        if enterprise_ids is not None:
            await self._projects.replace_enterprises(project.id, enterprise_ids, actor.id)
        project.updated_at = datetime.now(UTC)

        invalidation = AnalysisInvalidationService(self._session)
        changed_fields = set(payload.model_fields_set)
        if enterprise_ids is not None or "bid_deadline" in changed_fields:
            await invalidation.invalidate_inputs({project.id})
        elif changed_fields.intersection({"name", "purchaser", "project_type", "region"}):
            # 自定义风险模板允许读取这些项目字段；匹配本身不依赖它们。
            await invalidation.invalidate_after_match({project.id})
        else:
            await invalidation.invalidate_report({project.id})
        self._record_audit(actor.id, "UPDATE_PROJECT", project.id, project.id, str(before))
        return self._to_response(
            project,
            enterprise_ids
            if enterprise_ids is not None
            else await self._projects.list_enterprise_ids(project.id),
        )

    async def archive(self, project_id: UUID, actor: AuthenticatedUser) -> ProjectResponse:
        """归档为显式生命周期操作，归档后不能再修改项目内容。"""
        project, member = await self._get_authorized_project(project_id, actor)
        self._require_owner_or_admin(actor, member)
        self._require_writable(project)
        now = datetime.now(UTC)
        project.status = "ARCHIVED"
        project.archived_at = now
        project.updated_at = now
        self._record_audit(actor.id, "ARCHIVE_PROJECT", project.id, project.id)
        return self._to_response(project, await self._projects.list_enterprise_ids(project.id))

    async def delete(self, project_id: UUID, actor: AuthenticatedUser) -> None:
        """软删除项目，保留审计与历史业务记录但立即撤销常规访问入口。"""
        project, member = await self._get_authorized_project(project_id, actor)
        self._require_owner_or_admin(actor, member)
        now = datetime.now(UTC)
        project.status = "ARCHIVED"
        project.archived_at = now
        project.deleted_at = now
        project.updated_at = now
        self._record_audit(actor.id, "DELETE_PROJECT", project.id, project.id)

    async def list_members(
        self, project_id: UUID, actor: AuthenticatedUser
    ) -> list[ProjectMemberResponse]:
        """项目成员可查看协作名单，但不能据此绕过资源级授权。"""
        await self.require_project_access(project_id, actor)
        return [
            ProjectMemberResponse(
                user_id=member.user_id,
                username=user.username,
                display_name=user.display_name,
                role=member.role,
                created_at=member.created_at,
                updated_at=member.updated_at,
            )
            for member, user in await self._projects.list_members(project_id)
        ]

    async def list_assignable_users(
        self, project_id: UUID, actor: AuthenticatedUser
    ) -> list[ProjectAssignableUserResponse]:
        """仅负责人/管理员可为项目挑选活跃协作者，避免泄漏内部账户名单。"""
        _, member = await self._get_authorized_project(project_id, actor)
        self._require_owner_or_admin(actor, member)
        member_ids = {
            project_member.user_id
            for project_member, _ in await self._projects.list_members(project_id)
        }
        users = await IdentityRepository(self._session).list_active_users()
        return [
            ProjectAssignableUserResponse(
                user_id=user.id,
                username=user.username,
                display_name=user.display_name,
                is_member=user.id in member_ids,
            )
            for user in users
        ]

    async def upsert_member(
        self,
        project_id: UUID,
        user_id: UUID,
        actor: AuthenticatedUser,
        payload: ProjectMemberUpsertRequest,
    ) -> ProjectMemberResponse:
        """OWNER 或管理员为启用账户授予/调整 EDITOR、VIEWER 协作权限。"""
        project, member = await self._get_authorized_project(project_id, actor)
        self._require_owner_or_admin(actor, member)
        self._require_writable(project)
        user = await IdentityRepository(self._session).get_active_user(user_id)
        if user is None:
            raise DomainError("USER_NOT_FOUND", "用户不存在或已禁用", 404)
        if user_id == project.owner_id:
            raise DomainError("OWNER_ROLE_PROTECTED", "当前负责人不能通过成员接口修改角色", 409)
        now = datetime.now(UTC)
        target_member = await self._projects.get_member(project_id, user_id)
        if target_member is None:
            target_member = ProjectMember(
                project_id=project_id,
                user_id=user_id,
                role=payload.role,
                created_at=now,
                updated_at=now,
            )
            self._projects.add_member(target_member)
        else:
            target_member.role = payload.role
            target_member.updated_at = now
        self._record_audit(
            actor.id, "UPSERT_PROJECT_MEMBER", user_id, project_id, target_type="PROJECT_MEMBER"
        )
        return ProjectMemberResponse(
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=target_member.role,
            created_at=target_member.created_at,
            updated_at=target_member.updated_at,
        )

    async def remove_member(
        self, project_id: UUID, user_id: UUID, actor: AuthenticatedUser
    ) -> None:
        """OWNER 或管理员移除普通协作成员，当前负责人不能被此接口移除。"""
        project, member = await self._get_authorized_project(project_id, actor)
        self._require_owner_or_admin(actor, member)
        self._require_writable(project)
        if user_id == project.owner_id:
            raise DomainError("OWNER_ROLE_PROTECTED", "请先交接项目负责人，再移除原负责人", 409)
        target_member = await self._projects.get_member(project_id, user_id)
        if target_member is None:
            raise DomainError("PROJECT_MEMBER_NOT_FOUND", "项目成员不存在", 404)
        await self._projects.remove_member(target_member)
        self._record_audit(
            actor.id, "REMOVE_PROJECT_MEMBER", user_id, project_id, target_type="PROJECT_MEMBER"
        )

    async def transfer_ownership(
        self, project_id: UUID, user_id: UUID, actor: AuthenticatedUser
    ) -> ProjectMemberResponse:
        """显式交接唯一负责人：新负责人必须已经是该项目的普通成员。"""
        project, member = await self._get_authorized_project(project_id, actor)
        self._require_owner_or_admin(actor, member)
        self._require_writable(project)
        new_owner_member = await self._projects.get_member(project_id, user_id)
        if new_owner_member is None:
            raise DomainError("PROJECT_MEMBER_NOT_FOUND", "新负责人必须先加入项目", 409)
        if user_id == project.owner_id:
            raise DomainError("OWNER_UNCHANGED", "该用户已是项目负责人", 409)
        new_owner_user = await IdentityRepository(self._session).get_active_user(user_id)
        if new_owner_user is None:
            raise DomainError("USER_NOT_FOUND", "用户不存在或已禁用", 404)
        old_owner_member = await self._projects.get_member(project_id, project.owner_id)
        now = datetime.now(UTC)
        if old_owner_member is not None:
            old_owner_member.role = "EDITOR"
            old_owner_member.updated_at = now
        new_owner_member.role = PROJECT_OWNER
        new_owner_member.updated_at = now
        project.owner_id = user_id
        project.updated_at = now
        self._record_audit(
            actor.id,
            "TRANSFER_PROJECT_OWNERSHIP",
            user_id,
            project_id,
            target_type="PROJECT_MEMBER",
        )
        return ProjectMemberResponse(
            user_id=user_id,
            username=new_owner_user.username,
            display_name=new_owner_user.display_name,
            role=PROJECT_OWNER,
            created_at=new_owner_member.created_at,
            updated_at=new_owner_member.updated_at,
        )

    async def _resolve_code(self, requested_code: str | None) -> str:
        """校验自定义编号或生成稳定可读的项目编号。"""
        code = requested_code.upper() if requested_code else f"PRJ-{uuid4().hex[:12].upper()}"
        if await self._projects.get_by_code(code):
            raise DomainError("PROJECT_CODE_EXISTS", "项目编号已存在", 409)
        return code

    async def _get_authorized_project(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
    ) -> tuple[TenderProject, ProjectMember | None]:
        """统一执行存在性与成员授权，避免向无权用户泄漏资源是否存在。"""
        project = await self._projects.get_active(project_id)
        if project is None:
            raise DomainError("RESOURCE_NOT_FOUND", "项目不存在", 404)
        if self._is_system_admin(actor):
            return project, None
        member = await self._projects.get_member(project_id, actor.id)
        if member is None:
            raise DomainError("RESOURCE_NOT_FOUND", "项目不存在", 404)
        return project, member

    @staticmethod
    def _is_system_admin(actor: AuthenticatedUser) -> bool:
        return SYSTEM_ADMIN in actor.role_codes

    def _require_owner_or_admin(
        self,
        actor: AuthenticatedUser,
        member: ProjectMember | None,
    ) -> None:
        if not self._is_system_admin(actor) and (member is None or member.role != PROJECT_OWNER):
            raise DomainError("PERMISSION_DENIED", "仅项目负责人可执行该操作", 403)

    @staticmethod
    def _require_writable(project: TenderProject) -> None:
        if project.status == "ARCHIVED":
            raise DomainError("PROJECT_ARCHIVED", "归档项目不可修改", 409)

    def _record_audit(
        self,
        actor_id: UUID,
        action: str,
        target_id: UUID,
        project_id: UUID,
        before_summary: str | None = None,
        target_type: str = "TENDER_PROJECT",
    ) -> None:
        """审计摘要保持简短，不写入文件正文、令牌或原始敏感材料。"""
        self._session.add(
            AuditLog(
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                project_id=project_id,
                before_summary=before_summary,
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _to_response(project: TenderProject, enterprise_ids: list[UUID]) -> ProjectResponse:
        """ORM 不加载集合关系，显式传入企业 ID 以避免响应阶段发生隐式查询。"""
        values = {
            field: getattr(project, field)
            for field in ProjectResponse.model_fields
            if field != "enterprise_ids"
        }
        return ProjectResponse.model_validate({**values, "enterprise_ids": enterprise_ids})
