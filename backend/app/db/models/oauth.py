"""OAuth 绑定与登录日志模型。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SysUserOauth(Base):
    """用户第三方授权绑定（企业微信、钉钉、飞书等）。"""

    __tablename__ = "sys_user_oauth"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 关联系统用户
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    # 企业 ID（区分多租户）
    corp_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 企业微信成员 UserID（企业内部用户）
    wecom_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 非企业成员的 OpenID（游客）
    wecom_open_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 微信 UnionID（需绑定微信开放平台）
    union_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("corp_id", "wecom_user_id", name="uk_corp_wecom_userid"),
        UniqueConstraint("corp_id", "wecom_open_id", name="uk_corp_wecom_openid"),
    )


class SysLoginLog(Base):
    """登录日志。"""

    __tablename__ = "sys_login_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    login_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="wecom_qrcode",
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    browser: Mapped[str | None] = mapped_column(String(128), nullable=True)
    os: Mapped[str | None] = mapped_column(String(128), nullable=True)
    login_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    msg: Mapped[str | None] = mapped_column(String(255), nullable=True)
