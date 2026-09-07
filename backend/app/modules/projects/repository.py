"""项目域仓储：只执行项目及成员关系的数据读写。"""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import User
from app.modules.projects.models import (
    Enterprise,
    EnterpriseMember,
    ProjectEnterprise,
    ProjectMember,
    TenderProject,
)


class ProjectRepository:
    """项目数据访问入口；项目权限分支由 ProjectService 决定。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self, project_id: UUID) -> TenderProject | None:
        """读取未软删除项目；归档项目仍可被读取。"""
        statement = select(TenderProject).where(
            TenderProject.id == project_id,
            TenderProject.deleted_at.is_(None),
        )
        return await self._session.scalar(statement)

    async def get_by_code(self, code: str) -> TenderProject | None:
        """项目编号全局唯一，供创建服务提前返回可理解的冲突信息。"""
        return await self._session.scalar(select(TenderProject).where(TenderProject.code == code))

    async def list_visible(self, user_id: UUID, is_system_admin: bool) -> list[TenderProject]:
        """管理员查看全部，普通用户只能查看 project_members 中的项目。"""
        statement = select(TenderProject).where(TenderProject.deleted_at.is_(None))
        if not is_system_admin:
            statement = statement.join(
                ProjectMember,
                ProjectMember.project_id == TenderProject.id,
            ).where(ProjectMember.user_id == user_id)
        statement = statement.order_by(TenderProject.created_at.desc(), TenderProject.id)
        return list((await self._session.scalars(statement)).all())

    async def get_member(self, project_id: UUID, user_id: UUID) -> ProjectMember | None:
        """查询特定项目成员关系；这是普通用户资源授权的事实来源。"""
        statement = select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
        return await self._session.scalar(statement)

    async def list_members(self, project_id: UUID) -> list[tuple[ProjectMember, User]]:
        """返回项目成员及启用账户展示信息，授权判断仍由服务层先执行。"""
        statement = (
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.created_at, ProjectMember.user_id)
        )
        return list((await self._session.execute(statement)).all())

    async def get_active_enterprises(self, enterprise_ids: list[UUID]) -> list[Enterprise]:
        """只返回未删除且启用的企业，供创建项目时一次性校验选择结果。"""
        if not enterprise_ids:
            return []
        statement = select(Enterprise).where(
            Enterprise.id.in_(enterprise_ids),
            Enterprise.status == "ACTIVE",
            Enterprise.deleted_at.is_(None),
        )
        return list((await self._session.scalars(statement)).all())

    async def list_enterprises(self) -> list[Enterprise]:
        statement = (
            select(Enterprise)
            .where(Enterprise.deleted_at.is_(None))
            .order_by(Enterprise.name, Enterprise.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_enterprise(
        self, enterprise_id: UUID, *, for_update: bool = False
    ) -> Enterprise | None:
        statement = select(Enterprise).where(
            Enterprise.id == enterprise_id, Enterprise.deleted_at.is_(None)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def get_by_credit_code(self, credit_code: str) -> Enterprise | None:
        return await self._session.scalar(
            select(Enterprise).where(
                Enterprise.credit_code == credit_code,
                Enterprise.deleted_at.is_(None),
            )
        )

    async def list_enterprise_ids_for_projects(
        self, project_ids: list[UUID]
    ) -> dict[UUID, list[UUID]]:
        """批量读取项目企业绑定，避免项目列表按项目逐条查询。"""
        if not project_ids:
            return {}
        statement = (
            select(
                ProjectEnterprise.project_id,
                ProjectEnterprise.enterprise_id,
                ProjectEnterprise.is_lead,
            )
            .where(ProjectEnterprise.project_id.in_(project_ids))
            .order_by(
                ProjectEnterprise.project_id,
                ProjectEnterprise.is_lead.desc(),
                ProjectEnterprise.enterprise_id,
            )
        )
        output: dict[UUID, list[UUID]] = {}
        for project_id, enterprise_id, _is_lead in (await self._session.execute(statement)).all():
            output.setdefault(project_id, []).append(enterprise_id)
        return output

    async def list_enterprise_ids(self, project_id: UUID) -> list[UUID]:
        statement = (
            select(ProjectEnterprise.enterprise_id)
            .where(ProjectEnterprise.project_id == project_id)
            .order_by(ProjectEnterprise.is_lead.desc(), ProjectEnterprise.enterprise_id)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_active_project_ids_for_enterprise(self, enterprise_id: UUID) -> list[UUID]:
        """材料事实变化时，定位仍绑定该企业的项目。"""
        statement = (
            select(ProjectEnterprise.project_id)
            .join(TenderProject, TenderProject.id == ProjectEnterprise.project_id)
            .where(
                ProjectEnterprise.enterprise_id == enterprise_id,
                TenderProject.deleted_at.is_(None),
            )
        )
        return list((await self._session.scalars(statement)).all())

    async def has_active_project_binding(self, enterprise_id: UUID) -> bool:
        """检查企业是否仍被未删除项目使用，删除前不能只看材料表。"""
        statement = (
            select(ProjectEnterprise.project_id)
            .join(TenderProject, TenderProject.id == ProjectEnterprise.project_id)
            .where(
                ProjectEnterprise.enterprise_id == enterprise_id,
                TenderProject.deleted_at.is_(None),
            )
            .limit(1)
        )
        return await self._session.scalar(statement) is not None

    async def get_enterprise_member(
        self, enterprise_id: UUID, user_id: UUID
    ) -> EnterpriseMember | None:
        return await self._session.scalar(
            select(EnterpriseMember).where(
                EnterpriseMember.enterprise_id == enterprise_id, EnterpriseMember.user_id == user_id
            )
        )

    async def get_enterprise_member_by_id(
        self, enterprise_id: UUID, member_id: UUID
    ) -> EnterpriseMember | None:
        return await self._session.scalar(
            select(EnterpriseMember).where(
                EnterpriseMember.enterprise_id == enterprise_id, EnterpriseMember.id == member_id
            )
        )

    async def list_enterprise_members(
        self, enterprise_id: UUID
    ) -> list[tuple[EnterpriseMember, User]]:
        statement = (
            select(EnterpriseMember, User)
            .join(User, User.id == EnterpriseMember.user_id)
            .where(EnterpriseMember.enterprise_id == enterprise_id)
            .order_by(EnterpriseMember.created_at, EnterpriseMember.id)
        )
        return list((await self._session.execute(statement)).all())

    def add(self, project: TenderProject) -> None:
        """登记新增项目，具体提交由请求级 Session 生命周期控制。"""
        self._session.add(project)

    def add_member(self, member: ProjectMember) -> None:
        """登记项目成员关系。创建项目时负责人必须同时成为 OWNER。"""
        self._session.add(member)

    def add_enterprise(self, binding: ProjectEnterprise) -> None:
        self._session.add(binding)

    async def replace_enterprises(
        self,
        project_id: UUID,
        enterprise_ids: list[UUID],
        actor_id: UUID,
    ) -> None:
        """原子替换绑定集合；首个 ID 是牵头企业，顺序由请求明确给出。"""
        await self._session.execute(
            delete(ProjectEnterprise).where(ProjectEnterprise.project_id == project_id)
        )
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        self._session.add_all(
            [
                ProjectEnterprise(
                    project_id=project_id,
                    enterprise_id=enterprise_id,
                    is_lead=index == 0,
                    created_at=now,
                    created_by=actor_id,
                )
                for index, enterprise_id in enumerate(enterprise_ids)
            ]
        )

    def add_enterprise_entity(self, enterprise: Enterprise) -> None:
        self._session.add(enterprise)

    def add_enterprise_member(self, member: EnterpriseMember) -> None:
        self._session.add(member)

    async def remove_member(self, member: ProjectMember) -> None:
        """删除成员关系；调用方必须先确保不会移除当前 OWNER。"""
        await self._session.delete(member)
