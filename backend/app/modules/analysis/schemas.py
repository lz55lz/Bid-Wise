"""发现项分析 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class FindingAnalysisJobResponse(BaseModel):
    """任务状态始终以 PostgreSQL 为准，浏览器不直接轮询 Redis。"""

    id: UUID
    project_id: UUID
    status: str
    created_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
