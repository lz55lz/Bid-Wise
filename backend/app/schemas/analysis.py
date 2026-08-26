from datetime import datetime
from typing import Any
from uuid import UUID

from app.schemas.base import ApiSchema


class AnalysisRunResponse(ApiSchema):
    id: UUID
    project_id: UUID
    status: str
    current_stage: str
    task_id: UUID | None
    report_id: UUID | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    snapshot: dict[str, Any] | None = None
