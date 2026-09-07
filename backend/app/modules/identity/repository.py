"""身份域仓储：只封装数据访问，不包含登录或授权分支。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import RevokedAccessToken, SystemRole, User, UserSystemRole


class IdentityRepository:
    """用户与系统角色的异步数据访问入口。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_user_by_username(self, username: str) -> User | None:
        """按规范化用户名查找启用账户。"""
        statement = select(User).where(User.username == username, User.status == "ACTIVE")
        return await self._session.scalar(statement)

    async def get_user_by_username(self, username: str) -> User | None:
        """管理端创建账户前查询同名账户，禁用账户也必须保持用户名唯一。"""
        return await self._session.scalar(select(User).where(User.username == username))

    async def get_active_user(self, user_id: UUID) -> User | None:
        """令牌解析后回查用户，禁用用户不能继续使用旧令牌。"""
        statement = select(User).where(User.id == user_id, User.status == "ACTIVE")
        return await self._session.scalar(statement)

    async def get_user(self, user_id: UUID) -> User | None:
        """管理端按 ID 查询账号，禁用账号也需要可被审计和恢复。"""
        return await self._session.get(User, user_id)

    async def list_system_role_codes(self, user_id: UUID) -> set[str]:
        """返回用户当前角色；不从 JWT 读取，保证权限修改立即生效。"""
        statement = select(UserSystemRole.role_code).where(UserSystemRole.user_id == user_id)
        return set((await self._session.scalars(statement)).all())

    async def list_system_role_codes_by_users(
        self, user_ids: list[UUID]
    ) -> dict[UUID, set[str]]:
        """批量读取多个用户的系统角色，避免管理端用户列表出现 N+1 查询。"""
        if not user_ids:
            return {}
        statement = select(UserSystemRole.user_id, UserSystemRole.role_code).where(
            UserSystemRole.user_id.in_(user_ids)
        )
        rows = (await self._session.execute(statement)).all()
        role_map: dict[UUID, set[str]] = {user_id: set() for user_id in user_ids}
        for user_id, role_code in rows:
            role_map[user_id].add(role_code)
        return role_map

    async def list_system_roles(self) -> list[SystemRole]:
        return list(
            (await self._session.scalars(select(SystemRole).order_by(SystemRole.code))).all()
        )

    async def has_any_user(self) -> bool:
        """初始化管理员只能在空身份库执行，避免绕过日常用户管理授权。"""
        return (await self._session.scalar(select(User.id).limit(1))) is not None

    async def list_users(self) -> list[User]:
        """仅供系统管理员服务列出内部账户，不在此处做权限判断。"""
        statement = select(User).order_by(User.created_at, User.id)
        return list((await self._session.scalars(statement)).all())

    async def list_active_users(self) -> list[User]:
        """项目负责人选择协作者时只返回仍可登录的内部账户。"""
        statement = (
            select(User)
            .where(User.status == "ACTIVE")
            .order_by(User.display_name, User.username, User.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def count_active_users_with_role(self, role_code: str) -> int:
        """用于防止最后一个有效系统管理员被禁用或移除。"""
        statement = (
            select(func.count())
            .select_from(User)
            .join(UserSystemRole, UserSystemRole.user_id == User.id)
            .where(User.status == "ACTIVE", UserSystemRole.role_code == role_code)
        )
        return int((await self._session.scalar(statement)) or 0)

    def add_user(self, user: User, role_codes: set[str]) -> None:
        """持久化用户及其角色关联；提交时机由服务调用方统一控制。"""
        self._session.add(user)
        self._session.add_all(
            UserSystemRole(user_id=user.id, role_code=role_code, created_at=user.created_at)
            for role_code in role_codes
        )

    async def replace_system_roles(self, user: User, role_codes: set[str]) -> None:
        """用当前白名单角色整体替换用户系统角色，避免残留旧授权。"""
        await self._session.execute(delete(UserSystemRole).where(UserSystemRole.user_id == user.id))
        self._session.add_all(
            UserSystemRole(user_id=user.id, role_code=role_code, created_at=user.updated_at)
            for role_code in role_codes
        )

    async def is_access_token_revoked(self, jti: str) -> bool:
        """按随机 JWT 标识回查主动登出状态，完整令牌不会持久化。"""
        return (
            await self._session.scalar(
                select(RevokedAccessToken.jti).where(RevokedAccessToken.jti == jti)
            )
        ) is not None

    async def revoke_access_token(
        self,
        *,
        jti: str,
        user_id: UUID,
        expires_at: datetime,
        revoked_at: datetime,
    ) -> bool:
        """幂等登记登出令牌，同时清理到期记录以控制事实表增长。"""
        await self._session.execute(
            delete(RevokedAccessToken).where(RevokedAccessToken.expires_at <= revoked_at)
        )
        if await self.is_access_token_revoked(jti):
            return False
        self._session.add(
            RevokedAccessToken(
                jti=jti,
                user_id=user_id,
                expires_at=expires_at,
                revoked_at=revoked_at,
            )
        )
        return True
