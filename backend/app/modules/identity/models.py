"""身份域 ORM 模型。

系统角色表达“能执行哪一类业务动作”；项目成员角色则表达“能在某个项目中做什么”。
两类权限刻意拆开，避免旧系统中只要拥有 PROJECT_OWNER 角色就能管理任意项目的
越权问题。
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """可登录的内部用户；单企业部署，不引入 tenant_id。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="users_status_check"),
        UniqueConstraint("username"),
        Index("ix_users_username", "username"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 密码重置会推进此时间点，使此前签发的所有 JWT 在下一次服务端回查时立即失效。
    access_token_invalid_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SystemRole(Base):
    """系统级角色字典，只描述跨项目的管理或专业能力。"""

    __tablename__ = "system_roles"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(256), nullable=False)


class UserSystemRole(Base):
    """用户与系统角色的多对多关联；不保存项目级授权。"""

    __tablename__ = "user_system_roles"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role_code: Mapped[str] = mapped_column(ForeignKey("system_roles.code"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(Base):
    """关键操作的不可变审计记录；内容摘要由服务层脱敏后写入。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(index=True)
    project_id: Mapped[UUID | None] = mapped_column(index=True)
    before_summary: Mapped[str | None] = mapped_column(Text)
    after_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RevokedAccessToken(Base):
    """已主动登出的访问令牌标识。

    只保存不可逆且随机的 JWT `jti`，不保存完整令牌；记录会按过期时间清理，避免把 Redis
    当成不可审计的安全事实源。
    """

    __tablename__ = "revoked_access_tokens"

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
