from uuid import UUID

from sqlalchemy.orm import Session

from app.core.constants import SYSTEM_ADMIN
from app.core.errors import DomainError
from app.db.repositories.audit_repository import AuditRepository
from app.db.repositories.project_repository import ProjectRepository
from app.schemas.audit import AuditLogResponse


class AuditQueryService:
    def __init__(self, session: Session) -> None:
        self._audits = AuditRepository(session)
        self._projects = ProjectRepository(session)

    def list_for_actor(
        self, actor_id: UUID, role_codes: set[str], project_id: UUID | None
    ) -> list[AuditLogResponse]:
        if SYSTEM_ADMIN not in role_codes:
            if project_id is None or not self._projects.is_member(project_id, actor_id):
                raise DomainError("PERMISSION_DENIED", "无权查看审计日志", 403)
            project = self._projects.get(project_id)
            if project is None or project.owner_id != actor_id:
                raise DomainError("PERMISSION_DENIED", "仅项目负责人可查看本项目审计日志", 403)
        return [
            AuditLogResponse.model_validate(log, from_attributes=True)
            for log in self._audits.list(project_id)
        ]
