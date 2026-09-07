"""一键完整分析运行的持久化状态。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FindingAnalysisJob(Base):
    """LLM 发现项分析的冻结输入和异步状态。"""

    __tablename__ = "finding_analysis_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("tender_projects.id"), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    input_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectAnalysisRun(Base):
    """一次完整分析的业务级运行记录；保存冻结输入、阶段状态和失败原因。"""

    __tablename__ = "project_analysis_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'REPORT_QUEUED', 'SUCCEEDED', 'FAILED')",
            name="project_analysis_runs_status_check",
        ),
        Index(
            "ux_project_analysis_runs_active_project",
            "project_id",
            unique=True,
            postgresql_where="status IN ('QUEUED', 'RUNNING', 'REPORT_QUEUED')",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("tender_projects.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    current_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    stage_outputs: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
