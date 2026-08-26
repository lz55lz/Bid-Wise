import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import AuditLog


class AuditService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        actor_id: UUID | None,
        action: str,
        target_type: str,
        target_id: UUID | None = None,
        project_id: UUID | None = None,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
    ) -> None:
        self._session.add(
            AuditLog(
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                project_id=project_id,
                before_summary=json.dumps(before, ensure_ascii=False) if before else None,
                after_summary=json.dumps(after, ensure_ascii=False) if after else None,
                created_at=datetime.now(UTC),
            )
        )
