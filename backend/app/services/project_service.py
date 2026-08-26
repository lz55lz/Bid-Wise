from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.constants import PROJECT_OWNER, SYSTEM_ADMIN
from app.core.errors import DomainError
from app.db.models import ProjectMember, TenderProject
from app.db.repositories.identity_repository import IdentityRepository
from app.db.repositories.project_repository import ProjectRepository
from app.schemas.projects import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberResponse,
    ProjectResponse,
    ProjectUpdate,
)
from app.services.audit_service import AuditService


class ProjectService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._projects = ProjectRepository(session)
        self._identity = IdentityRepository(session)
        self._audit = AuditService(session)

    def create(self, actor_id: UUID, payload: ProjectCreate) -> ProjectResponse:
        # 自动生成项目编号
        code = payload.code
        if not code:
            code = f"PRJ{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}{uuid4().hex[:6].upper()}"
            attempts = 0
            while self._projects.get_by_code(code) and attempts < 10:
                code = f"PRJ{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}{uuid4().hex[:6].upper()}"
                attempts += 1
        self._validate_enterprise_ids(payload.enterprise_ids)
        now = datetime.now(UTC)
        # Handle deadline alias from frontend
        data = payload.model_dump()
        data["code"] = code
        if payload.deadline is not None and payload.bid_deadline is None:
            data["bid_deadline"] = payload.deadline
        project = TenderProject(
            **{k: v for k, v in data.items() if k not in ("deadline", "status", "enterprise_ids")},
            owner_id=actor_id,
            created_at=now,
            updated_at=now,
            status="DRAFT",
        )
        self._projects.add(project)
        self._session.flush()
        self._projects.replace_enterprises(project.id, payload.enterprise_ids, actor_id)
        self._projects.add_member(
            ProjectMember(
                project_id=project.id, user_id=actor_id, role_code=PROJECT_OWNER, created_at=now
            )
        )
        self._audit.record(
            actor_id=actor_id,
            action="CREATE_PROJECT",
            target_type="PROJECT",
            target_id=project.id,
            project_id=project.id,
        )
        self._session.commit()
        return self._to_response(project)

    def list_visible(self, actor_id: UUID, role_codes: set[str]) -> list[ProjectResponse]:
        return [
            self._to_response(project)
            for project in self._projects.list_visible(actor_id, SYSTEM_ADMIN in role_codes)
        ]

    def get_visible(self, project_id: UUID, actor_id: UUID, role_codes: set[str]) -> TenderProject:
        project = self._projects.get(project_id)
        if project is None or (
            SYSTEM_ADMIN not in role_codes and not self._projects.is_member(project_id, actor_id)
        ):
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        return project

    def update(
        self, project: TenderProject, actor_id: UUID, payload: ProjectUpdate, is_admin: bool = False
    ) -> ProjectResponse:
        self._require_manage(project, actor_id, is_admin)
        self._require_writable(project)
        # enterprise_ids 存在关联表中，不是 TenderProject 的字段。
        before = {
            field: (
                [str(item) for item in self._projects.list_enterprise_ids(project.id)]
                if field == "enterprise_ids"
                else getattr(project, field)
            )
            for field in payload.model_fields_set
        }
        data = payload.model_dump(exclude_unset=True)
        # Handle deadline alias from frontend
        if payload.deadline is not None and payload.bid_deadline is None:
            data["bid_deadline"] = payload.deadline
        enterprise_ids = data.pop("enterprise_ids", None)
        if enterprise_ids is not None:
            self._validate_enterprise_ids(enterprise_ids)
        data = {k: v for k, v in data.items() if k != "deadline"}
        for field, value in data.items():
            setattr(project, field, value)
        if enterprise_ids is not None:
            self._projects.replace_enterprises(project.id, enterprise_ids, actor_id)
        project.updated_at = datetime.now(UTC)
        self._audit.record(
            actor_id=actor_id,
            action="UPDATE_PROJECT",
            target_type="PROJECT",
            target_id=project.id,
            project_id=project.id,
            before=before,
        )
        self._session.commit()
        return self._to_response(project)

    def archive(
        self, project: TenderProject, actor_id: UUID, is_admin: bool = False
    ) -> ProjectResponse:
        self._require_manage(project, actor_id, is_admin)
        self._require_writable(project)
        project.status = "ARCHIVED"
        project.archived_at = datetime.now(UTC)
        project.updated_at = project.archived_at
        self._audit.record(
            actor_id=actor_id,
            action="ARCHIVE_PROJECT",
            target_type="PROJECT",
            target_id=project.id,
            project_id=project.id,
        )
        self._session.commit()
        return self._to_response(project)

    def delete(self, project: TenderProject, actor_id: UUID, is_admin: bool = False) -> None:
        self._require_manage(project, actor_id, is_admin)
        if project.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "项目不存在", 404)
        self._projects.delete(project.id)
        self._audit.record(
            actor_id=actor_id,
            action="DELETE_PROJECT",
            target_type="PROJECT",
            target_id=project.id,
            project_id=project.id,
        )
        self._session.commit()

    def add_member(
        self,
        project: TenderProject,
        actor_id: UUID,
        payload: ProjectMemberCreate,
        is_admin: bool = False,
    ) -> None:
        self._require_manage(project, actor_id, is_admin)
        self._require_writable(project)
        if self._identity.get_user(payload.user_id) is None:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        if not self._identity.role_exists(payload.role_code):
            raise DomainError("VALIDATION_ERROR", "包含不存在的角色", 422)
        if self._projects.has_member_role(project.id, payload.user_id, payload.role_code):
            raise DomainError("PROJECT_MEMBER_EXISTS", "项目成员角色已存在", 409)
        self._projects.add_member(
            ProjectMember(
                project_id=project.id,
                user_id=payload.user_id,
                role_code=payload.role_code,
                created_at=datetime.now(UTC),
            )
        )
        self._audit.record(
            actor_id=actor_id,
            action="ADD_PROJECT_MEMBER",
            target_type="PROJECT_MEMBER",
            project_id=project.id,
        )
        self._session.commit()

    def list_members(
        self, project: TenderProject, actor_id: UUID, is_admin: bool = False
    ) -> list[ProjectMemberResponse]:
        self._require_manage(project, actor_id, is_admin)
        return [
            ProjectMemberResponse(
                user_id=member.user_id,
                username=user.username,
                display_name=user.display_name,
                role_code=member.role_code,
                created_at=member.created_at,
            )
            for member, user in self._projects.list_members(project.id)
        ]

    def list_assignable_users(
        self, project: TenderProject, actor_id: UUID, is_admin: bool = False
    ) -> list[dict[str, str]]:
        self._require_manage(project, actor_id, is_admin)
        return [
            {"id": str(user.id), "username": user.username, "display_name": user.display_name}
            for user in self._identity.list_assignable_users()
        ]

    def list_enterprise_ids(self, project_id: UUID) -> list[UUID]:
        """项目绑定的投标企业 ID 集合(主投标人在前),供匹配/风险等下游服务使用。"""
        return self._projects.list_enterprise_ids(project_id)

    def _to_response(self, project: TenderProject) -> ProjectResponse:
        # enterprise_ids 是关联表投影字段,通过对象属性注入后交给 from_attributes
        project.enterprise_ids = self._projects.list_enterprise_ids(project.id)
        return ProjectResponse.model_validate(project, from_attributes=True)

    def _validate_enterprise_ids(self, enterprise_ids: list[UUID]) -> None:
        for enterprise_id in dict.fromkeys(enterprise_ids):
            if self._identity.get_enterprise(enterprise_id) is None:
                raise DomainError("VALIDATION_ERROR", "包含不存在的企业", 422)

    @staticmethod
    def _require_writable(project: TenderProject) -> None:
        if project.status == "ARCHIVED":
            raise DomainError("PROJECT_ARCHIVED", "归档项目不可修改", 409)

    @staticmethod
    def require_writable(project: TenderProject) -> None:
        ProjectService._require_writable(project)

    @staticmethod
    def _require_manage(project: TenderProject, actor_id: UUID, is_admin: bool) -> None:
        if not is_admin and project.owner_id != actor_id:
            raise DomainError("PERMISSION_DENIED", "仅项目负责人可执行该操作", 403)
