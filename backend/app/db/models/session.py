"""Session and Message models — port from WeKnora internal/types/session.go + message.go."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Session(Base):
    """Chat session — groups multiple user/assistant messages together."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="新对话")
    # 关联项目
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 会话所有者
    user_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    # 是否置顶
    is_pinned: Mapped[bool] = mapped_column(default=False)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # LangGraph 多轮状态：当前绑定的项目
    active_project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # LangGraph 多轮状态：未完成的意图（如等待项目选择）
    pending_intent: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 关联消息
    messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        "Message", foreign_keys="Message.session_id", back_populates="session"
    )


class Message(Base):
    """Chat message — stores user queries, assistant answers, and knowledge references."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 消息角色：user / assistant
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # 消息内容
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # RAG 引用（chunk id → evidence id → content 快照）
    knowledge_references: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 是否已完成
    is_completed: Mapped[bool] = mapped_column(default=True)
    # 是否是 fallback（无知识库匹配）
    is_fallback: Mapped[bool] = mapped_column(default=False)
    # 创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    session: Mapped["Session"] = relationship(
        "Session", foreign_keys=[session_id], back_populates="messages"
    )  # noqa: F821
