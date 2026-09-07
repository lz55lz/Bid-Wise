"""投标企业 API 契约。企业是项目可选的业务主体，不承担多租户职责。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EnterpriseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=256)
    credit_code: str | None = Field(default=None, min_length=1, max_length=18)
    enterprise_type: str | None = Field(default=None, max_length=32)


class EnterpriseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=256)
    credit_code: str | None = Field(default=None, min_length=1, max_length=18)
    enterprise_type: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, pattern="^(ACTIVE|DISABLED)$")


class EnterpriseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    credit_code: str | None
    enterprise_type: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class EnterpriseMemberCreateRequest(BaseModel):
    user_id: UUID
    role: str = Field(pattern="^(ADMIN|EDITOR|VIEWER)$")


class EnterpriseMemberUpdateRequest(BaseModel):
    role: str = Field(pattern="^(ADMIN|EDITOR|VIEWER)$")


class EnterpriseMemberResponse(BaseModel):
    id: UUID
    user_id: UUID
    username: str
    display_name: str
    role: str
    created_at: datetime
    updated_at: datetime
