"""项目发现项及其 Evidence 关联 ORM 模型。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectFinding(Base):
    """一个等待人工确认的项目需求、风险或建议。

    AI 可以提出候选，但绝不能直接把候选写成已确认业务结论；状态必须由有项目管理
    权限的用户明确切换。这样 Human-in-the-Loop 是可审计的领域流程，而非前端弹窗。
    """

    __tablename__ = "project_findings"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('REQUIREMENT', 'RISK', 'RECOMMENDATION')",
            name="project_findings_kind_check",
        ),
        CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="project_findings_severity_check",
        ),
        CheckConstraint(
            "status IN ('PENDING_REVIEW', 'CONFIRMED', 'DISMISSED', 'STALE')",
            name="project_findings_status_check",
        ),
        CheckConstraint("source IN ('MANUAL', 'AI')", name="project_findings_source_check"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FindingEvidence(Base):
    """发现项与可追溯证据的多对多关联。"""

    __tablename__ = "finding_evidences"

    finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_findings.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidences.id", ondelete="CASCADE"), primary_key=True
    )
