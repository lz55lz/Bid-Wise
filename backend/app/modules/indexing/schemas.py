"""Evidence 索引任务的 HTTP 响应契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class EvidenceIndexJobResponse(BaseModel):
    """前端轮询索引任务时所需的最小安全字段。"""

    id: UUID
    project_id: UUID
    status: str
    requested_count: int
    indexed_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
