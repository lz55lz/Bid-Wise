"""仅系统管理员可用的内部账户管理接口。"""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.schemas import (
    UserCreateRequest,
    UserPasswordResetRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.modules.identity.service import IdentityAdministrationService

router = APIRouter(prefix="/admin/users", tags=["系统用户管理"])


@router.get("/roles")
async def list_roles(current_user: CurrentUser, session: DatabaseSession) -> list[dict[str, str]]:
    """返回受控角色字典，前端不能自行发明角色码。"""
    if "SYSTEM_ADMIN" not in current_user.role_codes:
        from app.core.errors import DomainError

        raise DomainError("PERMISSION_DENIED", "仅系统管理员可查看角色字典", 403)
    return [
        {"code": role.code, "name": role.name, "description": role.description}
        for role in await IdentityRepository(session).list_system_roles()
    ]


@router.get("", response_model=list[UserResponse])
async def list_users(
    current_user: CurrentUser,
    session: DatabaseSession,
) -> list[UserResponse]:
    """仅系统管理员可列出内部账号。"""
    return await IdentityAdministrationService(session).list_users(current_user)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> UserResponse:
    """创建可加入项目的内部协作账户。"""
    return await IdentityAdministrationService(session).create_user(current_user, payload)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: UserUpdateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> UserResponse:
    """更新账号显示名、状态或系统角色。"""
    return await IdentityAdministrationService(session).update_user(current_user, user_id, payload)


@router.post("/{user_id}/reset-password", response_model=UserResponse)
async def reset_user_password(
    user_id: UUID,
    payload: UserPasswordResetRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> UserResponse:
    """重置内部协作账号密码。"""
    return await IdentityAdministrationService(session).reset_password(
        current_user, user_id, payload
    )
