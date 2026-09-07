"""项目报告 ORM 模型。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectReport(Base):
    """项目当前报告。

    一个项目只保留一条报告记录；重新生成时原子覆盖冻结输入和 Markdown 正文。
    """

    __tablename__ = "project_reports"
    __table_args__ = (
        CheckConstraint(
            "report_type IN ('SIMPLE', 'FULL', 'SUMMARY')",
            name="project_reports_report_type_check",
        ),
        CheckConstraint(
            "status IN ('QUEUED', 'GENERATING', 'READY', 'FAILED')",
            name="project_reports_status_check",
        ),
        UniqueConstraint("project_id", name="uq_project_reports_project"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id"), nullable=False, index=True
    )
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    analysis_run_id: Mapped[UUID | None] = mapped_column(index=True)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    content_markdown: Mapped[str | None] = mapped_column(Text)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    sections: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    citations: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
