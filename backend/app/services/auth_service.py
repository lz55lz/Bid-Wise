from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.db.repositories.identity_repository import IdentityRepository
from app.schemas.auth import (
    AssignableUserResponse,
    LoginResponse,
    ManagedUserResponse,
    RoleResponse,
    UserCreate,
    UserResponse,
    UserRoleUpdate,
)
from app.services.audit_service import AuditService


class AuthService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = IdentityRepository(session)
        self._audit = AuditService(session)

    def login(self, username: str, password: str) -> LoginResponse:
        user = self._users.get_user_by_username(username.lower())
        if user is None or not verify_password(password, user.password_hash):
            raise DomainError("AUTHENTICATION_FAILED", "用户名或密码无效", 401)
        if not self._settings.jwt_secret_key:
            raise DomainError("SERVICE_UNAVAILABLE", "认证服务未配置", 503)
        user.last_login_at = datetime.now(UTC)
        token, _, expires_at = create_access_token(
            str(user.id),
            self._settings.jwt_secret_key.get_secret_value(),
            self._settings.jwt_access_token_minutes,
        )
        role_codes = sorted(self._users.list_role_codes(user.id))
        self._audit.record(actor_id=user.id, action="LOGIN", target_type="USER", target_id=user.id)
        self._session.commit()
        return LoginResponse(
            access_token=token,
            expires_in=self._settings.jwt_access_token_minutes * 60,
            user=UserResponse(
                id=user.id, username=user.username, display_name=user.display_name, roles=role_codes
            ),
        )

    def create_user(self, actor_id: object, payload: UserCreate) -> ManagedUserResponse:
        if self._users.get_user_by_username(payload.username.lower()):
            raise DomainError("USERNAME_EXISTS", "用户名已存在", 409)
        unknown_roles = [role for role in payload.roles if not self._users.role_exists(role)]
        if unknown_roles:
            raise DomainError("VALIDATION_ERROR", "包含不存在的角色", 422)
        now = datetime.now(UTC)
        user = User(
            username=payload.username.lower(),
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
            created_at=now,
            updated_at=now,
        )
        self._users.add_user(user, payload.roles)
        self._audit.record(
            actor_id=actor_id, action="CREATE_USER", target_type="USER", target_id=user.id
        )
        self._session.commit()
        return self._managed_response(user, set(payload.roles))

    def list_users(self) -> list[ManagedUserResponse]:
        return [self._managed_response(user) for user in self._users.list_users()]

    def list_roles(self) -> list[RoleResponse]:
        return [
            RoleResponse.model_validate(role, from_attributes=True)
            for role in self._users.list_roles()
        ]

    def list_assignable_users(self) -> list[AssignableUserResponse]:
        return [
            AssignableUserResponse.model_validate(user, from_attributes=True)
            for user in self._users.list_assignable_users()
        ]

    def update_user_roles(
        self, actor_id: UUID, user_id: UUID, payload: UserRoleUpdate
    ) -> ManagedUserResponse:
        user = self._users.get_user_for_management(user_id)
        if user is None:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        unknown_roles = [role for role in payload.roles if not self._users.role_exists(role)]
        if unknown_roles:
            raise DomainError("VALIDATION_ERROR", "包含不存在的角色", 422)
        if actor_id == user.id and payload.status == "DISABLED":
            raise DomainError("USER_SELF_DISABLE_FORBIDDEN", "不能停用当前登录用户", 409)
        before = {"roles": sorted(self._users.list_role_codes(user.id)), "status": user.status}
        now = datetime.now(UTC)
        user.status = payload.status
        user.updated_at = now
        self._users.replace_roles(user.id, payload.roles, now)
        self._audit.record(
            actor_id=actor_id,
            action="UPDATE_USER_ROLES",
            target_type="USER",
            target_id=user.id,
            before=before,
            after={"roles": sorted(payload.roles), "status": payload.status},
        )
        self._session.commit()
        return self._managed_response(user, set(payload.roles))

    def _managed_response(
        self, user: User, role_codes: set[str] | None = None
    ) -> ManagedUserResponse:
        return ManagedUserResponse(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
            roles=sorted(
                role_codes if role_codes is not None else self._users.list_role_codes(user.id)
            ),
        )
