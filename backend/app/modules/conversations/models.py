"""项目内私有对话的 ORM 模型。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    """用户的全局法律或项目私有问答会话。

    ``project_id`` 为 NULL 时是全局法律会话；否则必须验证项目成员资格和创建人。
    """

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tender_projects.id"), nullable=True, index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationMessage(Base):
    """会话中不可变的一条用户或助手消息。

    citations 和 traces 是回答的可审计快照；真实授权仍每次通过项目与 Evidence 回查，
    不将这些 JSON 字段当作权限来源。
    """

    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('USER', 'ASSISTANT')",
            name="conversation_messages_role_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    traces: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
