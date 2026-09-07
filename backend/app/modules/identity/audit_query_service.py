"""审计日志只读查询。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.identity.models import AuditLog
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService


class AuditQueryService:
    """审计事实保存在 PostgreSQL，查询授权不依赖前端筛选条件。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self,
        actor: AuthenticatedUser,
        project_id: UUID | None,
        page: int,
        page_size: int,
    ) -> list[AuditLog]:
        if "SYSTEM_ADMIN" not in actor.role_codes:
            if project_id is None:
                raise DomainError("PERMISSION_DENIED", "非管理员必须指定项目审计范围", 403)
            # 项目管理权限仅授予 OWNER 或系统管理员，符合旧系统“负责人查项目审计”的规则。
            await ProjectService(self._session).require_project_management(project_id, actor)
        statement = select(AuditLog)
        if project_id is not None:
            statement = statement.where(AuditLog.project_id == project_id)
        statement = (
            statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list((await self._session.scalars(statement)).all())


def response(log: AuditLog) -> dict[str, object]:
    """返回审计索引信息；摘要由各写入服务提前脱敏后保存。"""
    return {
        "id": log.id,
        "actor_id": None if log.actor_id is None else str(log.actor_id),
        "action": log.action,
        "target_type": log.target_type,
        "target_id": None if log.target_id is None else str(log.target_id),
        "project_id": None if log.project_id is None else str(log.project_id),
        "before_summary": log.before_summary,
        "after_summary": log.after_summary,
        "created_at": log.created_at,
    }
