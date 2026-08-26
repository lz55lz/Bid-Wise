from datetime import datetime
from uuid import UUID

from app.schemas.base import ApiSchema


class AuditLogResponse(ApiSchema):
    id: int
    actor_id: UUID | None
    action: str
    target_type: str
    target_id: UUID | None
    project_id: UUID | None
    created_at: datetime
