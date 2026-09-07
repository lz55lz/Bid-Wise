"""长期记忆以 PostgreSQL 为事实源，不把 LangGraph checkpoint 当业务数据。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserMemory(Base):
    __tablename__ = "user_memories"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("tender_projects.id"), index=True)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False, default="PREFERENCE")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
