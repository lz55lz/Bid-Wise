from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import ProjectEnterprise, ProjectMember, TenderProject, User


class ProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, project_id: UUID) -> TenderProject | None:
        return self._session.get(TenderProject, project_id)

    def get_by_code(self, code: str) -> TenderProject | None:
        return self._session.scalar(select(TenderProject).where(TenderProject.code == code))

    def list_visible(self, user_id: UUID, is_admin: bool) -> list[TenderProject]:
        statement = (
            select(TenderProject)
            .where(TenderProject.deleted_at.is_(None))
            .order_by(TenderProject.created_at.desc())
        )
        if not is_admin:
            statement = (
                statement.join(ProjectMember)
                .where(ProjectMember.user_id == user_id)
                .distinct()
            )
        return list(self._session.scalars(statement))

    def is_member(self, project_id: UUID, user_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(ProjectMember.project_id).where(
                    ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
                )
            )
            is not None
        )

    def has_member_role(self, project_id: UUID, user_id: UUID, role_code: str) -> bool:
        return (
            self._session.scalar(
                select(ProjectMember.project_id).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user_id,
                    ProjectMember.role_code == role_code,
                )
            )
            is not None
        )

    def add(self, project: TenderProject) -> TenderProject:
        self._session.add(project)
        return project

    def add_member(self, member: ProjectMember) -> None:
        self._session.add(member)

    def list_members(self, project_id: UUID) -> list[tuple[ProjectMember, User]]:
        statement = (
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project_id)
            .order_by(User.display_name, User.username, ProjectMember.role_code)
        )
        return list(self._session.execute(statement).tuples())

    def delete(self, project_id: UUID) -> bool:
        project = self._session.get(TenderProject, project_id)
        if not project:
            return False
        project.deleted_at = datetime.now(UTC)
        self._session.flush()
        return True

    def list_enterprise_ids(self, project_id: UUID) -> list[UUID]:
        """项目绑定的投标企业 ID,主投标人(is_lead)排在最前。"""
        statement = (
            select(ProjectEnterprise.enterprise_id)
            .where(ProjectEnterprise.project_id == project_id)
            .order_by(ProjectEnterprise.is_lead.desc(), ProjectEnterprise.created_at)
        )
        return list(self._session.scalars(statement))

    def replace_enterprises(
        self, project_id: UUID, enterprise_ids: list[UUID], actor_id: UUID
    ) -> None:
        """整体替换项目绑定的企业集合,首个企业记为主投标人。"""
        self._session.execute(
            delete(ProjectEnterprise).where(ProjectEnterprise.project_id == project_id)
        )
        now = datetime.now(UTC)
        for index, enterprise_id in enumerate(dict.fromkeys(enterprise_ids)):
            self._session.add(
                ProjectEnterprise(
                    project_id=project_id,
                    enterprise_id=enterprise_id,
                    is_lead=index == 0,
                    created_at=now,
                    created_by=actor_id,
                )
            )
        self._session.flush()
