from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema


class LoginRequest(ApiSchema):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserResponse(ApiSchema):
    id: UUID
    username: str
    display_name: str
    roles: list[str]


class LoginResponse(ApiSchema):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until expiry
    user: UserResponse


class UserCreate(ApiSchema):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=128)
    roles: set[str] = Field(min_length=1)


class UserRoleUpdate(ApiSchema):
    roles: set[str] = Field(min_length=1)
    status: Literal["ACTIVE", "DISABLED"]


class ManagedUserResponse(UserResponse):
    status: Literal["ACTIVE", "DISABLED"]


class RoleResponse(ApiSchema):
    code: str
    name: str
    description: str


class AssignableUserResponse(ApiSchema):
    id: UUID
    username: str
    display_name: str
