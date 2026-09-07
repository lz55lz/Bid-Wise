"""项目 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreateRequest(BaseModel):
    """创建投标项目的最小必填信息。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=256)
    code: str | None = Field(default=None, min_length=1, max_length=128)
    purchaser: str = Field(min_length=1, max_length=256)
    project_type: str = Field(min_length=1, max_length=128)
    region: str = Field(min_length=1, max_length=128)
    # 至少选择一家投标企业；多个 ID 表示联合体。第一个企业为默认牵头方。
    enterprise_ids: list[UUID] = Field(min_length=1)
    bid_deadline: datetime | None = None


class ProjectUpdateRequest(BaseModel):
    """可修改的项目元数据；生命周期状态只允许通过专用归档接口变更。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=256)
    purchaser: str | None = Field(default=None, min_length=1, max_length=256)
    project_type: str | None = Field(default=None, min_length=1, max_length=128)
    region: str | None = Field(default=None, min_length=1, max_length=128)
    bid_deadline: datetime | None = None
    # 显式传入时整体替换项目投标企业；首个企业为联合体牵头方。
    enterprise_ids: list[UUID] | None = Field(default=None, min_length=1)


class ProjectResponse(BaseModel):
    """项目详情及列表响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    purchaser: str
    project_type: str
    region: str
    currency: str
    bid_deadline: datetime | None
    status: str
    owner_id: UUID
    enterprise_ids: list[UUID]
    created_at: datetime
    updated_at: datetime


class ProjectMemberUpsertRequest(BaseModel):
    """授予普通项目协作权限；OWNER 只能通过显式交接接口变更。"""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(EDITOR|VIEWER)$")


class ProjectMemberResponse(BaseModel):
    """项目成员展示信息。"""

    user_id: UUID
    username: str
    display_name: str
    role: str
    created_at: datetime
    updated_at: datetime


class ProjectAssignableUserResponse(BaseModel):
    """项目负责人选择协作者所需的最小账户信息。"""

    user_id: UUID
    username: str
    display_name: str
    is_member: bool
