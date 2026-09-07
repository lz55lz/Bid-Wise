"""身份 API 的请求与响应契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """密码登录请求；用户名仅允许部署管理员创建的内部账号。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CurrentUserResponse(BaseModel):
    """前端初始化和权限展示所需的当前用户信息。"""

    id: UUID
    username: str
    display_name: str
    roles: list[str]


class LoginResponse(BaseModel):
    """登录成功响应；访问令牌仅在 HTTPS 请求中由前端安全保存。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: CurrentUserResponse


class UserCreateRequest(BaseModel):
    """系统管理员创建内部协作账号；密码只用于本次写入，不会出现在响应或审计中。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=256)
    system_role_codes: list[str] = Field(default_factory=list, max_length=5)


class UserResponse(BaseModel):
    """管理端用户资料，不返回密码散列或令牌。"""

    id: UUID
    username: str
    display_name: str
    status: str
    system_role_codes: list[str]
    created_at: datetime
    last_login_at: datetime | None


class UserUpdateRequest(BaseModel):
    """系统管理员可变更的账号资料；密码必须走独立重置接口。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    status: str | None = Field(default=None, pattern="^(ACTIVE|DISABLED)$")
    system_role_codes: list[str] | None = Field(default=None, max_length=5)


class UserPasswordResetRequest(BaseModel):
    """管理员重置内部账号密码；明文仅用于本次散列。"""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=12, max_length=256)
