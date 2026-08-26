"""IM 集成数据模型。"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IMChannel(Base):
    """IM 渠道：把某个平台机器人绑定到某个项目。"""

    __tablename__ = "im_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # 平台类型：telegram/wecom/feishu/...
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 接入模式：webhook / websocket / longpoll
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="webhook")
    # 平台凭据（JSONB，列表接口不返回）
    credentials: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 机器人身份标识（去重，同一平台+身份唯一）
    bot_identity: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    # 绑定的 Agent（可选）
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 文件知识库（用户发文件自动入库）
    knowledge_base_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 输出模式：stream / full
    output_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="full")
    # 会话模式：user（按用户） / thread（按话题）
    session_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    # 是否启用
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 渠道配置的创建者（单企业部署，不引入 tenant 概念）
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __init__(self, **kwargs: Any) -> None:
        if "mode" not in kwargs:
            kwargs["mode"] = "webhook"
        if "credentials" not in kwargs:
            kwargs["credentials"] = {}
        if "output_mode" not in kwargs:
            kwargs["output_mode"] = "full"
        if "session_mode" not in kwargs:
            kwargs["session_mode"] = "user"
        if "enabled" not in kwargs:
            kwargs["enabled"] = True
        super().__init__(**kwargs)


class IMChannelSession(Base):
    """IM 渠道会话映射：(platform, user, chat, thread) → WeKnora session。"""

    __tablename__ = "im_channel_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    channel_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("im_channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform_user_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    chat_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
