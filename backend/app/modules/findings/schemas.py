"""发现项 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FindingCreateRequest(BaseModel):
    """人工录入的候选发现项；必须附带当前项目的 Evidence。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: str = Field(pattern="^(REQUIREMENT|RISK|RECOMMENDATION)$")
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(min_length=1, max_length=20_000)
    severity: str = Field(pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class FindingReviewRequest(BaseModel):
    """人工复核动作；只允许从待复核转为确认或驳回。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: str = Field(pattern="^(CONFIRMED|DISMISSED)$")
    review_note: str | None = Field(default=None, max_length=2_000)


class FindingResponse(BaseModel):
    """对外展示的发现项与关联 Evidence ID。"""

    id: UUID
    project_id: UUID
    kind: str
    title: str
    description: str
    severity: str
    status: str
    source: str
    evidence_ids: list[UUID]
    created_by: UUID
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    updated_at: datetime
