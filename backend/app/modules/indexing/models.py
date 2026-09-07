"""Evidence 向量索引任务的持久化事实模型。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EvidenceIndexJob(Base):
    """一次不可变的 Evidence 向量索引请求。

    队列只携带任务 ID；待索引 Evidence ID 快照留在 PostgreSQL，既可审计也能防止把业务
    文本或资源范围放进 Redis。原 Evidence 若因重解析被删除，Worker 会安全跳过它。
    """

    __tablename__ = "evidence_index_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="evidence_index_jobs_status_check",
        ),
        Index(
            "ux_evidence_index_jobs_active_project",
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id"), nullable=False, index=True
    )
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
