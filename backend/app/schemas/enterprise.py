from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema


class EnterpriseCreate(ApiSchema):
    name: str = Field(min_length=1, max_length=256)
    credit_code: str | None = Field(default=None, max_length=18)
    enterprise_type: str | None = Field(default=None, max_length=32)


class EnterpriseUpdate(ApiSchema):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    credit_code: str | None = Field(default=None, max_length=18)
    enterprise_type: str | None = Field(default=None, max_length=32)
    status: str | None = None


class EnterpriseResponse(ApiSchema):
    id: UUID
    name: str
    credit_code: str | None
    enterprise_type: str | None
    status: str
    created_at: datetime
    created_by: UUID
    qualifications: list | None = None
    past_projects: list | None = None
    financials: list | None = None
    personnel: list | None = None
    awards: list | None = None
    blacklist_status: list | None = None


class EnterpriseMemberCreate(ApiSchema):
    user_id: UUID
    role_code: str = Field(min_length=1, max_length=32)


class EnterpriseMemberUpdate(ApiSchema):
    role_code: str | None = Field(default=None, min_length=1, max_length=32)
    status: str | None = None


class EnterpriseMemberResponse(ApiSchema):
    id: UUID
    enterprise_id: UUID
    user_id: UUID
    username: str | None
    display_name: str | None
    role_code: str
    status: str
    created_at: datetime


class EnterpriseWithMembersResponse(ApiSchema):
    id: UUID
    name: str
    credit_code: str | None
    enterprise_type: str | None
    status: str
    created_at: datetime
    created_by: UUID
    members: list[EnterpriseMemberResponse]


class EnterpriseMaterialCreate(ApiSchema):
    enterprise_id: UUID
    material_type: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=512)
    material_no: str | None = Field(default=None, max_length=128)
    issuer: str | None = Field(default=None, max_length=256)
    level: str | None = Field(default=None, max_length=128)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    amount: float | None = Field(default=None, ge=0)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    attributes: dict = Field(default_factory=dict)


class EnterpriseMaterialResponse(ApiSchema):
    id: UUID
    enterprise_id: UUID | None
    material_type: str
    name: str
    material_no: str | None
    issuer: str | None
    level: str | None
    valid_from: datetime | None
    valid_to: datetime | None
    amount: float | None
    currency: str
    attributes: dict
    status: str
    created_at: datetime
    created_by: UUID
