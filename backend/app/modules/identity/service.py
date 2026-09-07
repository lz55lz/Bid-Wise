"""身份域应用服务。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.modules.identity.models import AuditLog, User
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.schemas import (
    CurrentUserResponse,
    LoginResponse,
    UserCreateRequest,
    UserPasswordResetRequest,
    UserResponse,
    UserUpdateRequest,
)

SYSTEM_ADMIN = "SYSTEM_ADMIN"
_SYSTEM_ROLE_CODES = frozenset(
    {"SYSTEM_ADMIN", "BID_SPECIALIST", "LEGAL_COMPLIANCE", "MATERIAL_ADMIN", "READ_ONLY"}
)


_DUMMY_PASSWORD_HASH = hash_password("bid-wise-nonexistent-account-dummy-password")


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """供 API 依赖和各领域服务使用的可信当前用户上下文。"""

    id: UUID
    username: str
    display_name: str
    role_codes: frozenset[str]


class AuthService:
    """登录与令牌解析用例；事务由外层数据库依赖统一提交或回滚。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._repository = IdentityRepository(session)
        self._settings = settings

    async def login(self, username: str, password: str) -> LoginResponse:
        """校验账户密码并签发令牌，失败信息保持一致以防用户名枚举。"""
        user = await self._repository.get_active_user_by_username(username.lower())
        password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
        password_valid = verify_password(password, password_hash)
        if user is None or not password_valid:
            raise DomainError("AUTHENTICATION_FAILED", "用户名或密码无效", 401)
        if self._settings.jwt_secret_key is None:
            raise DomainError("SERVICE_UNAVAILABLE", "认证服务未配置", 503)

        user.last_login_at = datetime.now(UTC)
        role_codes = await self._repository.list_system_role_codes(user.id)
        token, expires_at = create_access_token(
            str(user.id),
            self._settings.jwt_secret_key.get_secret_value(),
            self._settings.jwt_access_token_minutes,
        )
        return LoginResponse(
            access_token=token,
            expires_in=int((expires_at - datetime.now(UTC)).total_seconds()),
            user=CurrentUserResponse(
                id=user.id,
                username=user.username,
                display_name=user.display_name,
                roles=sorted(role_codes),
            ),
        )

    async def resolve(self, access_token: str) -> AuthenticatedUser:
        """验签后回查启用用户与实时系统角色，拒绝过期或已禁用账号。"""
        if self._settings.jwt_secret_key is None:
            raise DomainError("AUTHENTICATION_FAILED", "未登录或登录状态已失效", 401)
        payload = decode_access_token(
            access_token,
            self._settings.jwt_secret_key.get_secret_value(),
        )
        try:
            user_id = UUID(str(payload["sub"]))
            jti = str(payload["jti"])
            issued_at = self._token_timestamp(payload, "iat")
        except (KeyError, ValueError) as exc:
            raise DomainError("AUTHENTICATION_FAILED", "登录状态无效或已过期", 401) from exc
        user = await self._repository.get_active_user(user_id)
        if user is None:
            raise DomainError("AUTHENTICATION_FAILED", "未登录或登录状态已失效", 401)
        if await self._repository.is_access_token_revoked(jti):
            raise DomainError("AUTHENTICATION_FAILED", "未登录或登录状态已失效", 401)
        if user.access_token_invalid_after and issued_at <= user.access_token_invalid_after:
            raise DomainError("AUTHENTICATION_FAILED", "未登录或登录状态已失效", 401)
        return AuthenticatedUser(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role_codes=frozenset(await self._repository.list_system_role_codes(user.id)),
        )

    async def logout(self, access_token: str) -> None:
        """撤销当前 JWT；重复调用保持幂等，且从不把完整令牌写进审计或数据库。"""
        if self._settings.jwt_secret_key is None:
            raise DomainError("AUTHENTICATION_FAILED", "未登录或登录状态已失效", 401)
        payload = decode_access_token(
            access_token,
            self._settings.jwt_secret_key.get_secret_value(),
        )
        try:
            user_id = UUID(str(payload["sub"]))
            jti = str(payload["jti"])
            expires_at = self._token_timestamp(payload, "exp")
        except (KeyError, ValueError) as exc:
            raise DomainError("AUTHENTICATION_FAILED", "登录状态无效或已过期", 401) from exc
        now = datetime.now(UTC)
        added = await self._repository.revoke_access_token(
            jti=jti,
            user_id=user_id,
            expires_at=expires_at,
            revoked_at=now,
        )
        if added:
            self._session.add(
                AuditLog(
                    actor_id=user_id,
                    action="LOGOUT",
                    target_type="ACCESS_TOKEN",
                    target_id=None,
                    after_summary="主动注销当前访问令牌",
                    created_at=now,
                )
            )
        await self._session.commit()

    @staticmethod
    def _token_timestamp(payload: dict[str, object], claim: str) -> datetime:
        """把 PyJWT 的数字时间声明统一转为 UTC 时间，拒绝异常载荷。"""
        raw_value = payload[claim]
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"JWT {claim} 非数字时间")
        try:
            return datetime.fromtimestamp(raw_value, UTC)
        except (OSError, OverflowError, ValueError) as exc:
            raise ValueError(f"JWT {claim} 时间无效") from exc


class IdentityAdministrationService:
    """仅承载身份管理员用例；首个管理员由本地 CLI 调用。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = IdentityRepository(session)

    async def bootstrap_system_admin(
        self,
        username: str,
        password: str,
        display_name: str,
    ) -> User:
        """仅在用户表为空时创建唯一的首个系统管理员。"""
        normalized_username = username.lower()
        # CLI 理论上也可能被两个部署实例同时执行；数据库事务锁保证只能有一个
        # bootstrap 观察到空用户表。
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('bidwise:bootstrap-admin'))")
        )
        if await self._repository.has_any_user():
            raise DomainError("BOOTSTRAP_FORBIDDEN", "系统已存在用户，不能再次初始化管理员", 409)
        if len(password) < 12:
            raise DomainError("VALIDATION_ERROR", "管理员密码至少需要 12 个字符", 422)
        now = datetime.now(UTC)
        user = User(
            username=normalized_username,
            password_hash=hash_password(password),
            display_name=display_name,
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
        self._session.add(user)
        await self._session.flush()
        self._repository.add_user(user, {"SYSTEM_ADMIN"})
        self._session.add(
            AuditLog(
                actor_id=user.id,
                action="BOOTSTRAP_SYSTEM_ADMIN",
                target_type="USER",
                target_id=user.id,
                after_summary="首个系统管理员初始化",
                created_at=now,
            )
        )
        return user

    async def create_user(
        self, actor: AuthenticatedUser, payload: UserCreateRequest
    ) -> UserResponse:
        """仅系统管理员可创建内部账户，并校验角色代码不能由前端任意扩展。"""
        if SYSTEM_ADMIN not in actor.role_codes:
            raise DomainError("PERMISSION_DENIED", "仅系统管理员可创建用户", 403)
        username = payload.username.lower()
        if await self._repository.get_user_by_username(username):
            raise DomainError("USERNAME_EXISTS", "用户名已存在", 409)
        role_codes = set(payload.system_role_codes)
        invalid_codes = role_codes.difference(_SYSTEM_ROLE_CODES)
        if invalid_codes:
            raise DomainError("VALIDATION_ERROR", "包含无效系统角色", 422)
        now = datetime.now(UTC)
        user = User(
            username=username,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
        # 先 flush 出 user.id，再写角色关联；UserSystemRole 的外键直接读取 user.id，
        # 若等统一 flush，关联行会以 NULL user_id 落库（与 bootstrap 流程保持一致）。
        self._session.add(user)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise DomainError("USERNAME_EXISTS", "用户名已存在", 409) from exc
        self._repository.add_user(user, role_codes)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise DomainError("USERNAME_EXISTS", "用户名已存在", 409) from exc
        self._session.add(
            AuditLog(
                actor_id=actor.id,
                action="CREATE_USER",
                target_type="USER",
                target_id=user.id,
                after_summary=f"创建账号 {username}",
                created_at=now,
            )
        )
        return self._to_user_response(user, role_codes)

    async def list_users(self, actor: AuthenticatedUser) -> list[UserResponse]:
        """系统管理员查看所有内部账户及实时系统角色。"""
        if SYSTEM_ADMIN not in actor.role_codes:
            raise DomainError("PERMISSION_DENIED", "仅系统管理员可查看用户", 403)
        users = await self._repository.list_users()
        role_map = await self._repository.list_system_role_codes_by_users(
            [user.id for user in users]
        )
        return [self._to_user_response(user, role_map.get(user.id, set())) for user in users]

    async def update_user(
        self, actor: AuthenticatedUser, user_id: UUID, payload: UserUpdateRequest
    ) -> UserResponse:
        """管理员更新账号资料、状态或系统角色，并保护最后一个有效管理员。"""
        self._require_system_admin(actor)
        if not payload.model_fields_set:
            raise DomainError("VALIDATION_ERROR", "至少提供一个待更新字段", 422)
        user = await self._repository.get_user(user_id)
        if user is None:
            raise DomainError("USER_NOT_FOUND", "用户不存在", 404)
        current_roles = await self._repository.list_system_role_codes(user.id)
        requested_roles = (
            set(payload.system_role_codes)
            if payload.system_role_codes is not None
            else current_roles
        )
        self._validate_role_codes(requested_roles)
        requested_status = payload.status if payload.status is not None else user.status
        removes_active_admin = (
            SYSTEM_ADMIN in current_roles
            and user.status == "ACTIVE"
            and (requested_status != "ACTIVE" or SYSTEM_ADMIN not in requested_roles)
        )
        if removes_active_admin:
            # 串行化“最后一个管理员”保护检查，防止两个管理员同时把彼此禁用/降权。
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('bidwise:system-admin-guard'))")
            )
        active_admin_count = await self._repository.count_active_users_with_role(SYSTEM_ADMIN)
        if removes_active_admin and active_admin_count <= 1:
            raise DomainError(
                "LAST_SYSTEM_ADMIN_PROTECTED", "不能禁用或移除最后一个有效系统管理员", 409
            )
        if user.id == actor.id and requested_status == "DISABLED":
            raise DomainError("SELF_DISABLE_FORBIDDEN", "不能禁用当前登录账号", 409)
        now = datetime.now(UTC)
        if payload.display_name is not None:
            user.display_name = payload.display_name
        user.status = requested_status
        user.updated_at = now
        if payload.system_role_codes is not None:
            await self._repository.replace_system_roles(user, requested_roles)
        self._session.add(
            AuditLog(
                actor_id=actor.id,
                action="UPDATE_USER",
                target_type="USER",
                target_id=user.id,
                after_summary="更新账号资料、状态或系统角色",
                created_at=now,
            )
        )
        return self._to_user_response(user, requested_roles)

    async def reset_password(
        self,
        actor: AuthenticatedUser,
        user_id: UUID,
        payload: UserPasswordResetRequest,
    ) -> UserResponse:
        """管理员重置密码并立即使历史 JWT 失效，避免旧登录态继续可用。"""
        self._require_system_admin(actor)
        user = await self._repository.get_user(user_id)
        if user is None:
            raise DomainError("USER_NOT_FOUND", "用户不存在", 404)
        user.password_hash = hash_password(payload.password)
        user.updated_at = datetime.now(UTC)
        user.access_token_invalid_after = user.updated_at
        role_codes = await self._repository.list_system_role_codes(user.id)
        self._session.add(
            AuditLog(
                actor_id=actor.id,
                action="RESET_USER_PASSWORD",
                target_type="USER",
                target_id=user.id,
                created_at=user.updated_at,
            )
        )
        return self._to_user_response(user, role_codes)

    @staticmethod
    def _require_system_admin(actor: AuthenticatedUser) -> None:
        if SYSTEM_ADMIN not in actor.role_codes:
            raise DomainError("PERMISSION_DENIED", "仅系统管理员可执行该操作", 403)

    @staticmethod
    def _validate_role_codes(role_codes: set[str]) -> None:
        if role_codes.difference(_SYSTEM_ROLE_CODES):
            raise DomainError("VALIDATION_ERROR", "包含无效系统角色", 422)

    @staticmethod
    def _to_user_response(user: User, role_codes: set[str]) -> UserResponse:
        return UserResponse(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
            system_role_codes=sorted(role_codes),
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )
