from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from app.schemas.base import ApiSchema


class ProjectCreate(ApiSchema):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=256)
    code: str | None = Field(default=None, max_length=128)
    purchaser: str = Field(min_length=1, max_length=256)
    project_type: str = Field(min_length=1, max_length=128)
    region: str = Field(min_length=1, max_length=128)
    bid_deadline: datetime | None = None
    deadline: datetime | None = Field(default=None, deprecated="Use bid_deadline instead")
    enterprise_ids: list[UUID] = Field(min_length=1)  # 投标企业(联合体),首个为主投标人
    status: str | None = None


class ProjectUpdate(ApiSchema):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    purchaser: str | None = Field(default=None, min_length=1, max_length=256)
    project_type: str | None = Field(default=None, min_length=1, max_length=128)
    region: str | None = Field(default=None, min_length=1, max_length=128)
    bid_deadline: datetime | None = None
    deadline: datetime | None = Field(default=None, deprecated="Use bid_deadline instead")
    enterprise_ids: list[UUID] | None = Field(default=None, min_length=1)


class ProjectResponse(ApiSchema):
    id: UUID
    name: str
    code: str
    purchaser: str
    project_type: str
    region: str
    bid_deadline: datetime | None = None
    status: str
    owner_id: UUID
    enterprise_ids: list[UUID]


class ProjectMemberCreate(ApiSchema):
    user_id: UUID
    role_code: str = Field(min_length=1, max_length=40)


class ProjectMemberResponse(ApiSchema):
    user_id: UUID
    username: str
    display_name: str
    role_code: str
    created_at: datetime


class PipelineStatusResponse(ApiSchema):
    next: list[str]
    is_interrupted: bool
    values: dict[str, object]
