from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, project_id: UUID | None = None) -> list[AuditLog]:
        statement = select(AuditLog).order_by(AuditLog.created_at.desc())
        if project_id is not None:
            statement = statement.where(AuditLog.project_id == project_id)
        return list(self._session.scalars(statement))
